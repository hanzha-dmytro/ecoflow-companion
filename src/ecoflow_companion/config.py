"""User settings: defaults, validation and conversion to service configuration.

Reading a settings file never fails: a missing or invalid value falls back to its default and is
reported, so a damaged file cannot stop the application from starting. Storage is in
``infrastructure/storage``.
"""

import math
import re
import typing
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields

from ecoflow_companion.application.event_rules import (
    BatteryRuleConfig,
    EventRules,
    GridRuleConfig,
    LinkRuleConfig,
)
from ecoflow_companion.application.monitor_service import MonitorConfig
from ecoflow_companion.application.notification_service import NotificationPreferences
from ecoflow_companion.application.ports import StationSelector
from ecoflow_companion.application.reconnect_policy import ReconnectPolicy

SETTINGS_VERSION = 1

_ADDRESS_RE = re.compile(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$", re.IGNORECASE)


def _between(lo: float, hi: float) -> dict[str, Callable[[object], bool]]:
    return {"check": lambda v: lo <= v <= hi}  # type: ignore[operator]


def _valid_thresholds(value: object) -> bool:
    return isinstance(value, tuple) and 1 <= len(value) <= 5 and all(1 <= t <= 99 for t in value)


@dataclass(frozen=True, slots=True)
class DeviceSettings:
    # Pinned station; both empty: the only station in range (then pinned automatically)
    preferred_address: str | None = field(
        default=None, metadata={"check": lambda v: v is None or bool(_ADDRESS_RE.match(v))}
    )
    preferred_serial: str | None = None
    # Display only
    preferred_name: str | None = None


@dataclass(frozen=True, slots=True)
class WidgetSettings:
    always_on_top: bool = False
    close_to_tray: bool = True
    visible: bool = True
    x: int | None = None
    y: int | None = None


@dataclass(frozen=True, slots=True)
class NotificationSettings:
    enabled: bool = True
    battery_thresholds: tuple[int, ...] = field(
        default=(30, 20, 10), metadata={"check": _valid_thresholds}
    )
    battery_hysteresis_percent: int = field(default=3, metadata=_between(1, 50))
    grid_state: bool = True
    ble_disconnect: bool = True
    ble_reconnect: bool = True


@dataclass(frozen=True, slots=True)
class MonitoringSettings:
    stale_after_seconds: float = field(default=30.0, metadata=_between(5, 600))
    reconnect_max_delay_seconds: float = field(default=60.0, metadata=_between(5, 600))
    grid_debounce_seconds: float = field(default=5.0, metadata=_between(0, 120))
    ble_loss_confirm_seconds: float = field(default=15.0, metadata=_between(0, 600))


@dataclass(frozen=True, slots=True)
class StartupSettings:
    launch_at_login: bool = False


@dataclass(frozen=True, slots=True)
class Settings:
    device: DeviceSettings = DeviceSettings()
    widget: WidgetSettings = WidgetSettings()
    notifications: NotificationSettings = NotificationSettings()
    monitoring: MonitoringSettings = MonitoringSettings()
    startup: StartupSettings = StartupSettings()

    def to_dict(self) -> dict[str, object]:
        return {"version": SETTINGS_VERSION, **asdict(self)}

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> tuple["Settings", list[str]]:
        """Settings from parsed JSON, and a list of values that were invalid and replaced."""
        problems: list[str] = []
        hints = typing.get_type_hints(cls)
        sections = {}
        for section in fields(cls):
            raw = data.get(section.name, {})
            if not isinstance(raw, dict):
                problems.append(f"{section.name}: not an object, using defaults")
                raw = {}
            sections[section.name] = _parse_section(
                hints[section.name], raw, section.name, problems
            )
        return cls(**sections), problems


def _parse_section[T](cls: type[T], raw: dict, prefix: str, problems: list[str]) -> T:
    hints = typing.get_type_hints(cls)
    values = {}
    for f in fields(cls):  # type: ignore[arg-type]
        if f.name not in raw:
            continue
        value = _coerce(hints[f.name], raw[f.name])
        check = f.metadata.get("check")
        if value is _INVALID or (check is not None and value is not None and not check(value)):
            problems.append(f"{prefix}.{f.name}: invalid value, using default")
            continue
        values[f.name] = value
    return cls(**values)


_INVALID = object()


def _is_int(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _coerce(hint: object, value: object) -> object:
    """``value`` converted to the annotated type, or ``_INVALID``."""
    if hint is bool:
        return value if isinstance(value, bool) else _INVALID
    if hint is int:
        return value if _is_int(value) else _INVALID
    if hint is float:
        ok = (_is_int(value) or isinstance(value, float)) and math.isfinite(value)  # type: ignore[arg-type]
        return float(value) if ok else _INVALID  # type: ignore[arg-type]
    if hint == str | None:
        return value if value is None or isinstance(value, str) else _INVALID
    if hint == int | None:
        return value if value is None or _is_int(value) else _INVALID
    if hint == tuple[int, ...]:
        if isinstance(value, list | tuple) and all(_is_int(v) for v in value):
            return tuple(sorted(set(value), reverse=True))
        return _INVALID
    raise TypeError(f"unsupported settings type {hint!r}")


def selector_from(settings: Settings) -> StationSelector:
    return StationSelector(
        address=settings.device.preferred_address, serial=settings.device.preferred_serial
    )


def rules_from(settings: Settings) -> EventRules:
    n, m = settings.notifications, settings.monitoring
    return EventRules(
        BatteryRuleConfig(n.battery_thresholds, n.battery_hysteresis_percent),
        GridRuleConfig(m.grid_debounce_seconds),
        LinkRuleConfig(m.ble_loss_confirm_seconds),
    )


def monitor_config_from(settings: Settings) -> MonitorConfig:
    return MonitorConfig(stale_after_seconds=settings.monitoring.stale_after_seconds)


def policy_from(settings: Settings) -> ReconnectPolicy:
    return ReconnectPolicy(max_delay=settings.monitoring.reconnect_max_delay_seconds)


def notification_preferences_from(settings: Settings) -> NotificationPreferences:
    n = settings.notifications
    return NotificationPreferences(
        enabled=n.enabled,
        grid=n.grid_state,
        link_lost=n.ble_disconnect,
        link_restored=n.ble_reconnect,
    )
