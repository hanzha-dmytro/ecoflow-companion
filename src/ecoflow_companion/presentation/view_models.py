"""What the widget and tray show, computed without Qt so it can be unit-tested."""

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from ecoflow_companion.domain.models import (
    ConnectionState,
    FailureKind,
    LinkStatus,
    ModelSupport,
    StationInfo,
    StationSnapshot,
)

UNKNOWN = "—"


class Tone(StrEnum):
    OK = "ok"
    WARN = "warn"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


LINK_TEXT = {
    LinkStatus.CONNECTED: "Connected",
    LinkStatus.CONNECTING: "Connecting…",
    LinkStatus.RECONNECTING: "Reconnecting…",
    LinkStatus.DISCONNECTED: "Disconnected",
}

LINK_TONE = {
    LinkStatus.CONNECTED: Tone.OK,
    LinkStatus.CONNECTING: Tone.WARN,
    LinkStatus.RECONNECTING: Tone.WARN,
    LinkStatus.DISCONNECTED: Tone.UNKNOWN,
}

FAILURE_HINTS = {
    FailureKind.NO_CREDENTIALS: "Enter your EcoFlow user ID in Settings",
    FailureKind.BLUETOOTH_UNAVAILABLE: "Bluetooth is off or unavailable",
    FailureKind.NOT_FOUND: (
        "Station not found: is it on, nearby, and not connected to the EcoFlow app?"
    ),
    FailureKind.AMBIGUOUS: "Several stations in range: choose one in Settings",
    FailureKind.UNSUPPORTED_MODEL: "This station model is not supported",
    FailureKind.BUSY: "Station busy: close the EcoFlow app and other tools",
    FailureKind.AUTH_FAILED: "Station rejected the user ID: check it in Settings",
    FailureKind.TIMEOUT: "Connection timed out",
    FailureKind.LINK_LOST: "Connection lost",
    FailureKind.NO_TELEMETRY: "Station stopped sending data",
    FailureKind.ERROR: "Connection error",
}

SUPPORT_BADGE = {
    ModelSupport.TESTED: None,
    ModelSupport.UPSTREAM_ONLY: "Unverified model",
    ModelSupport.UNSUPPORTED: "Unsupported model",
    ModelSupport.SIMULATED: "SIMULATED",
}

TOOLTIP_LIMIT = 127  # Windows truncates tray tooltips beyond this


@dataclass(frozen=True, slots=True)
class StationView:
    title: str
    badge: str | None
    battery_text: str
    battery_level: int | None
    battery_tone: Tone
    ac_in_text: str
    ac_out_text: str
    grid_text: str
    grid_tone: Tone
    charging: bool  # from the grid; drawn as a bolt on the plug
    link_text: str
    link_tone: Tone
    stale: bool
    footer: tuple[str, ...]
    tooltip: str


def _watts(value: float | None) -> str:
    return UNKNOWN if value is None else f"{value:.0f} W"


def _time(at: datetime, today: date) -> str:
    local = at.astimezone()
    return local.strftime("%H:%M:%S" if local.date() == today else "%d %b %H:%M")


def battery_tone(level: int | None, thresholds: tuple[int, ...]) -> Tone:
    if level is None or not thresholds:
        return Tone.UNKNOWN if level is None else Tone.OK
    if level <= min(thresholds):
        return Tone.CRITICAL
    if level <= max(thresholds):
        return Tone.WARN
    return Tone.OK


def build_view(
    snapshot: StationSnapshot,
    connection: ConnectionState,
    station: StationInfo | None,
    *,
    thresholds: tuple[int, ...] = (30, 20, 10),
    today: date | None = None,
) -> StationView:
    today = today or datetime.now().astimezone().date()
    level = snapshot.battery_percent
    battery = UNKNOWN if level is None else f"{level}%"
    grid_text, grid_tone = {
        True: ("Grid connected", Tone.OK),
        False: ("Grid disconnected", Tone.CRITICAL),
        None: ("Grid unknown", Tone.UNKNOWN),
    }[snapshot.ac_plugged_in]
    stale = snapshot.stale
    # Charging from solar with the grid down gets no bolt: the plug stands for the grid
    charging = not stale and snapshot.ac_plugged_in is True and snapshot.battery_charging is True
    if charging:
        grid_text += " · charging"

    footer: list[str] = []
    if connection.error is not None and not connection.connected:
        footer.append(FAILURE_HINTS[connection.error])
    if stale and snapshot.last_update is not None:
        footer.append(f"Last update {_time(snapshot.last_update, today)}")
    elif stale and connection.connected:
        footer.append("Waiting for data…")

    title = station.model if station and station.model else "EcoFlow station"
    badge = SUPPORT_BADGE[station.support] if station else None
    link_text = LINK_TEXT[connection.status]

    if stale:
        values = f"{battery} (stale)" if level is not None else UNKNOWN
    else:
        values = f"{battery} · in {_watts(snapshot.ac_input_watts)}"
        values += f" · out {_watts(snapshot.ac_output_watts)}"
    tooltip = f"{title}\n{values}\n{grid_text if not stale else 'Grid unknown'} · {link_text}"

    return StationView(
        title=title,
        badge=badge,
        battery_text=battery,
        battery_level=level,
        battery_tone=Tone.UNKNOWN if stale else battery_tone(level, thresholds),
        ac_in_text=_watts(snapshot.ac_input_watts),
        ac_out_text=_watts(snapshot.ac_output_watts),
        grid_text=grid_text,
        grid_tone=Tone.UNKNOWN if stale else grid_tone,
        charging=charging,
        link_text=link_text,
        link_tone=LINK_TONE[connection.status],
        stale=stale,
        footer=tuple(footer),
        tooltip=tooltip[:TOOLTIP_LIMIT],
    )
