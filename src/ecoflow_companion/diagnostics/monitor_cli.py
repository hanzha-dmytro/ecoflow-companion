"""``ecoflow-diag monitor``: run the monitor service and print its events.

Exercises the same connection lifecycle, reconnect policy and alert rules the widget will
use, against a real station or the simulated one (``--mock``).
"""

import argparse
import asyncio
import contextlib
from collections.abc import Callable
from datetime import datetime, timedelta

from ecoflow_companion.application.event_rules import (
    BatteryRuleConfig,
    EventRules,
    GridRuleConfig,
    LinkRuleConfig,
)
from ecoflow_companion.application.monitor_service import MonitorConfig, MonitorService
from ecoflow_companion.application.ports import StationAdapter, StationSelector
from ecoflow_companion.domain.events import (
    BatteryLow,
    ConnectionChanged,
    DomainEvent,
    GridLost,
    GridRestored,
    LinkLost,
    LinkRestored,
    SnapshotUpdated,
    StationIdentified,
)
from ecoflow_companion.domain.models import StationSnapshot
from ecoflow_companion.infrastructure.ecoflow.eflib_adapter import EflibAdapter
from ecoflow_companion.infrastructure.ecoflow.mock_adapter import MockStationAdapter
from ecoflow_companion.infrastructure.windows.clock import AwakeClock
from ecoflow_companion.redaction import Redactor, mask_address, mask_serial

UNKNOWN = "—"


def _time(at: datetime | None) -> str:
    return at.astimezone().strftime("%H:%M:%S") if at is not None else UNKNOWN


def _now() -> str:
    return datetime.now().astimezone().strftime("%H:%M:%S")


def _duration(value: timedelta) -> str:
    minutes, seconds = divmod(round(value.total_seconds()), 60)
    return f"{minutes}m {seconds:02d}s" if minutes else f"{seconds}s"


def format_snapshot(s: StationSnapshot) -> str:
    def watts(v: float | None) -> str:
        return UNKNOWN if v is None else f"{v:.0f} W"

    battery = UNKNOWN if s.battery_percent is None else f"{s.battery_percent}%"
    grid = {True: "connected", False: "disconnected", None: "unknown"}[s.ac_plugged_in]
    freshness = f"STALE, last update {_time(s.last_update)}" if s.stale else "live"
    return (
        f"Battery {battery} | AC in {watts(s.ac_input_watts)} | "
        f"AC out {watts(s.ac_output_watts)} | Grid {grid} | {freshness}"
    )


def format_event(event: DomainEvent) -> str | None:
    """One line for events worth printing immediately; None for snapshots."""
    match event:
        case ConnectionChanged(state):
            error = f" ({state.error}: {state.error_detail})" if state.error else ""
            return f"BLE {state.status}{error}"
        case StationIdentified(station):
            return (
                f"Station: {station.model or 'unknown model'} [{station.support}], "
                f"serial {mask_serial(station.serial)}, address {mask_address(station.address)}"
            )
        case BatteryLow(percent, threshold, _, initial):
            note = " (already low at start)" if initial else ""
            return f"ALERT battery low: {percent}% (threshold {threshold}%){note}"
        case GridLost():
            return "ALERT grid power lost"
        case GridRestored():
            return "ALERT grid power restored"
        case LinkLost(at, reason):
            return f"ALERT BLE connection lost at {_time(at)} ({reason})"
        case LinkRestored(_, outage):
            return f"ALERT BLE connection restored after {_duration(outage)}"
    return None


def _parse_thresholds(text: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in text.split(",") if part.strip())
    except ValueError:
        raise argparse.ArgumentTypeError("thresholds must be comma-separated integers") from None


def add_parser(sub: argparse._SubParsersAction) -> argparse.ArgumentParser:
    p = sub.add_parser("monitor", help="run the monitor service and print its events")
    p.add_argument("--address", help="BLE address of the station (needed if several)")
    p.add_argument("--mock", action="store_true", help="use the simulated station")
    p.add_argument("--duration", type=float, default=600.0, help="seconds to run")
    p.add_argument("--interval", type=float, default=10.0, help="seconds between snapshots")
    p.add_argument("--thresholds", type=_parse_thresholds, default=(30, 20, 10))
    p.add_argument("--hysteresis", type=int, default=3)
    p.add_argument("--grid-debounce", type=float, default=5.0, help="seconds")
    p.add_argument("--link-confirm", type=float, default=15.0, help="seconds")
    p.add_argument("--stale-after", type=float, default=30.0, help="seconds")
    return p


async def cmd_monitor(
    args: argparse.Namespace, redactor: Redactor, read_user_id: Callable[[], str | None]
) -> int:
    user_id = "mock" if args.mock else read_user_id()
    if not user_id:
        print("EcoFlow user ID is required. Set ECOFLOW_USER_ID or run interactively.")
        return 2
    if not args.mock:
        redactor.add_secret(user_id)

    clock = AwakeClock()
    adapter: StationAdapter = (
        MockStationAdapter(clock=clock)
        if args.mock
        else EflibAdapter(redactor, clock=clock, debug_logging=args.debug)
    )
    try:
        rules = EventRules(
            BatteryRuleConfig(args.thresholds, args.hysteresis),
            GridRuleConfig(args.grid_debounce),
            LinkRuleConfig(args.link_confirm),
        )
    except ValueError as e:
        print(f"Invalid rule settings: {e}")
        return 2

    latest: list[StationSnapshot] = []

    def sink(event: DomainEvent) -> None:
        if isinstance(event, SnapshotUpdated):
            latest[:] = [event.snapshot]
        elif (line := format_event(event)) is not None:
            print(redactor.apply(f"{_now()} {line}"), flush=True)

    service = MonitorService(
        adapter,
        StationSelector(address=args.address),
        lambda: user_id,
        sink,
        config=MonitorConfig(stale_after_seconds=args.stale_after),
        rules=rules,
        clock=clock,
    )
    print(f"Monitoring for {args.duration:g} s (Ctrl+C to stop).")
    task = asyncio.create_task(service.run())
    loop = asyncio.get_running_loop()
    deadline = loop.time() + args.duration
    try:
        while loop.time() < deadline and not task.done():
            await asyncio.wait({task}, timeout=min(args.interval, deadline - loop.time()))
            if latest:
                print(f"{_now()} {format_snapshot(latest[0])}", flush=True)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    return 0
