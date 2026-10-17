"""BLE diagnostic probe for EcoFlow power stations (SPEC phase 1).

    ecoflow-diag scan    [--timeout S]
    ecoflow-diag probe   [--address ADDR] [--duration S] [--interval S] [--all-fields] [--debug]
                         [--experimental-profile MODULE]
    ecoflow-diag monitor [--address ADDR] [--mock] [--duration S] [--interval S] [--debug]

``scan`` lists nearby EcoFlow stations and whether the vendored upstream library has a
device class for their serial-number prefix. ``probe`` connects, authenticates and prints
sanitized telemetry until the duration elapses or Ctrl+C is pressed. ``monitor`` runs the
monitor service (reconnects and alert rules) and prints its events.

The EcoFlow user ID needed for authentication is read from the ``ECOFLOW_USER_ID``
environment variable or prompted for with hidden input. It is never accepted on the
command line, printed or logged.
"""

import argparse
import asyncio
import contextlib
import getpass
import logging
import os
import sys
import time
from collections import Counter
from datetime import datetime

from bleak.exc import BleakError
from google.protobuf.message import DecodeError

from ecoflow_companion._vendor import eflib
from ecoflow_companion._vendor.eflib import devices as eflib_devices
from ecoflow_companion._vendor.eflib.connection import ConnectionState
from ecoflow_companion._vendor.eflib.devicebase import DeviceBase
from ecoflow_companion._vendor.eflib.devices._delta3_base import Delta3Base
from ecoflow_companion._vendor.eflib.logging_util import LogOptions
from ecoflow_companion._vendor.eflib.packet import Packet
from ecoflow_companion._vendor.eflib.pb import pd335_sys_pb2
from ecoflow_companion._vendor.eflib.props.transforms import flow_is_on
from ecoflow_companion.diagnostics import monitor_cli
from ecoflow_companion.infrastructure.ecoflow.discovery import Found, discover
from ecoflow_companion.redaction import (
    RedactingFormatter,
    Redactor,
    mask_address,
    mask_device_name,
    mask_serial,
)

USER_ID_ENV = "ECOFLOW_USER_ID"

# Upstream field names (eflib/devices/_delta3_base.py) for the values the widget needs.
# They exist only on device classes that define them; absence is reported, never guessed.
CORE_FIELDS = (
    ("battery_level", "Battery", "%"),
    ("ac_input_power", "AC in", "W"),
    ("ac_output_power", "AC out", "W"),
    ("plugged_in_ac", "AC plugged in", ""),
)

# DELTA 3 telemetry frame (see Delta3Base.data_parse) and the protobuf fields that could signal
# grid presence. Upstream maps only plug_in_info_ac_charger_flag (as plugged_in_ac); upstream
# issues #300 and #469 report it missing on some DELTA 3 models/firmware, so all candidates are
# measured here instead of assumed.
DELTA3_TELEMETRY = (0x02, 0xFE, 0x15)
GRID_CANDIDATES = (
    ("plug_in_info_ac_charger_flag", "upstream plugged_in_ac"),
    ("plug_in_info_ac_in_flag", "not mapped upstream"),
    ("flow_info_ac_in", "not mapped upstream"),
    ("pow_get_ac_in", "upstream ac_input_power"),
)

_NOT_DEFINED = object()

log = logging.getLogger("ecoflow_companion.diag")
redactor = Redactor()


def describe(index: int, item: Found) -> str:
    ident = item.ident
    name = mask_device_name(item.adv.local_name) if item.adv.local_name else "-"
    lines = [
        f"[{index}] address {mask_address(item.ble_device.address)}  "
        f"name {name}  RSSI {item.adv.rssi} dBm",
        f"    serial prefix: {ident.serial[:4]}  (serial {mask_serial(ident.serial)})",
    ]
    if ident.upstream_class is not None:
        lines.append(f"    upstream device class: {ident.upstream_module} ({ident.upstream_model})")
    else:
        lines.append(
            f"    upstream device class: NONE for prefix '{ident.serial[:4]}' "
            "- telemetry decoding for this model is NOT supported upstream"
        )
    if ident.table_name:
        lines.append(
            f"    upstream name table: {ident.table_name} (packets {ident.table_packets}; "
            "prefix lookup only, not a compatibility claim)"
        )
    else:
        lines.append("    upstream name table: no entry")
    return "\n".join(lines)


async def cmd_scan(args: argparse.Namespace) -> int:
    print(f"Scanning for EcoFlow devices for {args.timeout:g} s...")
    try:
        found = await discover(args.timeout, redactor)
    except (BleakError, OSError) as e:
        print(f"Bluetooth scan failed: {redactor.apply(str(e))}")
        print("Check that Bluetooth is turned on in Windows Settings > Bluetooth & devices.")
        return 2
    if not found:
        print("No EcoFlow devices found. Make sure the station is on, nearby, and not")
        print("connected to another BLE client (for example the EcoFlow app on a phone).")
        return 1
    print(f"Found {len(found)} EcoFlow device(s):")
    for i, item in enumerate(found, 1):
        print(describe(i, item))
    return 0


def read_user_id() -> str | None:
    if user_id := os.environ.get(USER_ID_ENV, "").strip():
        return user_id
    if not sys.stdin.isatty():
        return None
    return getpass.getpass("EcoFlow user ID (input hidden): ").strip() or None


def select_target(found: list[Found], address: str | None) -> Found | None:
    if address:
        wanted = address.replace("-", ":").upper()
        return next((f for f in found if f.ble_device.address.upper() == wanted), None)
    if len(found) == 1:
        return found[0]
    return None


def build_device(target: Found, experimental_profile: str | None) -> DeviceBase:
    if experimental_profile is None:
        # Upstream picks the class by serial prefix, or UnsupportedDevice if none matches
        device = eflib.NewDevice(target.ble_device, target.adv)
        if device is None:
            raise RuntimeError("upstream could not create a device from the advertisement")
        return device

    modules = {m.__name__.rsplit(".", 1)[-1]: m for m in eflib_devices.devices}
    module = modules.get(experimental_profile)
    device_cls = getattr(module, "Device", None)
    if device_cls is None:
        names = ", ".join(sorted(n for n, m in modules.items() if hasattr(m, "Device")))
        raise SystemExit(f"Unknown profile '{experimental_profile}'. Available: {names}")
    return device_cls(target.ble_device, target.adv, target.ident.serial)


def format_value(device: DeviceBase, field: str, unit: str) -> str:
    value = getattr(device, field, _NOT_DEFINED)
    if value is _NOT_DEFINED:
        return "n/a (not defined by profile)"
    if value is None:
        return "unknown"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        value = round(value, 1)
    return f"{value} {unit}".rstrip()


class PacketStats:
    """Header-level tally of received packets; payloads are not recorded."""

    def __init__(self) -> None:
        self.headers: Counter[tuple[int, int, int, int, int]] = Counter()
        self.total = 0
        self.invalid = 0
        self.last_at: float | None = None

    def on_packet(self, packet: Packet) -> None:
        if not packet:  # upstream InvalidPacket is falsy
            self.invalid += 1
            return
        self.total += 1
        self.last_at = time.monotonic()
        version = getattr(packet, "version", 0)
        self.headers[(version, packet.src, packet.dst, packet.cmd_set, packet.cmd_id)] += 1

    def report(self) -> str:
        if not self.headers:
            return "  (no packets parsed)"
        rows = ["  ver  src   dst   cmd_set cmd_id  count"]
        for (ver, src, dst, cmd_set, cmd_id), count in self.headers.most_common():
            rows.append(
                f"  v{ver:<3} 0x{src:02X}  0x{dst:02X}  0x{cmd_set:02X}    "
                f"0x{cmd_id:02X}    {count}"
            )
        if self.invalid:
            rows.append(f"  invalid/unparsable packets: {self.invalid}")
        return "\n".join(rows)


def describe_grid_value(name: str, value: object) -> str:
    if name == "flow_info_ac_in" and isinstance(value, int):
        return f"{value} (bits {value:b}, upstream flow_is_on={flow_is_on(value)})"
    if isinstance(value, float):
        return f"{value:.1f}"
    return repr(value)


class GridSignalStats:
    """Presence and values of grid-state candidate fields in DELTA 3 telemetry frames.

    Frames are partial updates: a field absent from one frame keeps its last value. A field
    absent from every frame is never reported by this unit/firmware.
    """

    def __init__(self) -> None:
        self.frames = 0
        self.decode_errors = 0
        self.present: Counter[str] = Counter()
        self.values: dict[str, Counter[str]] = {name: Counter() for name, _ in GRID_CANDIDATES}
        self.last: dict[str, object] = {}
        self.changes: list[str] = []

    def on_packet(self, packet: Packet) -> None:
        if not packet or (packet.src, packet.cmd_set, packet.cmd_id) != DELTA3_TELEMETRY:
            return
        try:
            msg = pd335_sys_pb2.DisplayPropertyUpload.FromString(packet.payload)
        except DecodeError:
            self.decode_errors += 1
            return
        self.frames += 1
        now = datetime.now().astimezone().strftime("%H:%M:%S")
        for name, _ in GRID_CANDIDATES:
            if not msg.HasField(name):
                continue
            value = getattr(msg, name)
            self.present[name] += 1
            if name != "pow_get_ac_in":
                self.values[name][describe_grid_value(name, value)] += 1
            # Power fluctuates constantly; only log it crossing zero
            key = (value > 0) if name == "pow_get_ac_in" else value
            if name in self.last and self.last[name] != key:
                old = self.last[name]
                self.changes.append(f"  {now} {name}: {old!r} -> {key!r}")
            self.last[name] = key

    def report(self) -> str:
        if self.frames == 0:
            return "  (no DELTA 3 telemetry frames received)"
        rows = [f"  telemetry frames: {self.frames}, decode errors: {self.decode_errors}"]
        for name, note in GRID_CANDIDATES:
            rows.append(f"  {name} ({note}): present in {self.present[name]}/{self.frames}")
            for value, count in self.values[name].most_common():
                rows.append(f"      {value} x{count}")
        rows.append("  changes (pow_get_ac_in shown as > 0):")
        rows.extend(self.changes or ["      none"])
        return "\n".join(rows)


def print_snapshot(device: DeviceBase, stats: PacketStats, unverified: bool) -> None:
    now = datetime.now().astimezone().strftime("%H:%M:%S")
    age = "never" if stats.last_at is None else f"{time.monotonic() - stats.last_at:.1f}s ago"
    parts = [f"{label}: {format_value(device, f, unit)}" for f, label, unit in CORE_FIELDS]
    prefix = "[UNVERIFIED PROFILE] " if unverified else ""
    print(f"{now} {prefix}" + " | ".join(parts) + f" | packets: {stats.total}, last {age}")


def print_all_fields(device: DeviceBase) -> None:
    fields = getattr(device, "_fields", None)
    if not fields:
        print("Device profile defines no decoded fields.")
        return
    print("All decoded fields (non-empty):")
    for f in fields:
        name = f.public_name
        if name.endswith("_sn") or "serial" in name:
            continue
        value = getattr(device, name, None)
        # Control entities (buttons) are registered as fields too; they are not telemetry
        if value is not None and not callable(value):
            print(redactor.apply(f"  {name}: {value!r}"))


async def cmd_probe(args: argparse.Namespace) -> int:
    user_id = read_user_id()
    if not user_id:
        print(f"EcoFlow user ID is required. Set {USER_ID_ENV} or run interactively.")
        return 2
    if not user_id.isdigit():
        print("EcoFlow user ID must be numeric.")
        return 2
    redactor.add_secret(user_id)

    print(f"Scanning for EcoFlow devices for {args.scan_timeout:g} s...")
    try:
        found = await discover(args.scan_timeout, redactor)
    except (BleakError, OSError) as e:
        print(f"Bluetooth scan failed: {redactor.apply(str(e))}")
        return 2
    target = select_target(found, args.address)
    if target is None:
        if not found:
            print("No EcoFlow devices found.")
        else:
            print("Several devices found (or --address did not match); pass --address:")
            for i, item in enumerate(found, 1):
                print(describe(i, item))
        return 1

    redactor.add_credentials(user_id, target.ident.serial)
    print(describe(1, target))

    unverified = args.experimental_profile is not None
    if target.ident.upstream_class is None and not unverified:
        print(
            "\nNo upstream device class for this serial prefix: the probe will authenticate\n"
            "and record packet headers only. No telemetry values can be decoded."
        )
    if unverified:
        print(
            f"\nWARNING: forcing upstream profile '{args.experimental_profile}'. This is an\n"
            "experiment: decoded values are UNVERIFIED for this model and may be wrong."
        )

    device = build_device(target, args.experimental_profile)
    # Not LogOptions.MASKED: upstream's own mask runs first and leaves two address octets
    # visible; RedactingFormatter masks the full values instead.
    options = LogOptions.CONNECTION_DEBUG if args.debug else LogOptions(0)
    # The application, not upstream, will own reconnects (SPEC 5.3), so the probe disables them
    device.with_logging_options(options).with_disabled_reconnect(True)

    stats = PacketStats()
    device.on_packet_parsed(stats.on_packet)
    grid = GridSignalStats() if isinstance(device, Delta3Base) else None
    if grid is not None:
        device.on_packet_parsed(grid.on_packet)
    disconnected = asyncio.Event()
    device.on_disconnect(lambda _exc: disconnected.set())
    authenticated = False

    try:
        print("\nConnecting and authenticating...")
        try:
            async with asyncio.timeout(args.connect_timeout):
                await device.connect(user_id=user_id, max_attempts=args.attempts)
                state, exc = await device.wait_until_authenticated_or_error(return_exc=True)
        except TimeoutError:
            state, exc = device.connection_state, None
            print(f"Timed out after {args.connect_timeout:g} s (last state: {state}).")
            return 3

        if state is not ConnectionState.AUTHENTICATED:
            print(f"Connection failed: state {state}")
            if exc is not None:
                print(f"  reason: {redactor.apply(f'{type(exc).__name__}: {exc}')}")
            if state is ConnectionState.ERROR_AUTH_FAILED:
                print(
                    "  Check the user ID and that the station is bound to that account in the\n"
                    "  official EcoFlow app."
                )
            return 3

        authenticated = True
        print(f"Authenticated. Reading telemetry for {args.duration:g} s (Ctrl+C to stop).\n")
        deadline = time.monotonic() + args.duration
        while time.monotonic() < deadline and not disconnected.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(disconnected.wait(), timeout=args.interval)
            print_snapshot(device, stats, unverified)

        if disconnected.is_set():
            print("\nBLE connection was lost.")
        return 0 if not disconnected.is_set() else 4
    finally:
        # Also reached on Ctrl+C, so an interrupted run still prints what it collected
        if authenticated:
            print("\nReceived packet headers:")
            print(stats.report())
            if grid is not None:
                print("\nGrid-signal candidates:")
                print(grid.report())
            if args.all_fields:
                print()
                print_all_fields(device)
        print("\nDisconnecting...")
        try:
            async with asyncio.timeout(15):
                await device.disconnect()
        except TimeoutError:
            print("Disconnect did not finish within 15 s.")


def configure_logging(debug: bool) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        RedactingFormatter(redactor, "%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )
    # Filter on the handler: upstream sets INFO/DEBUG on its own loggers in with_logging_options
    handler.setLevel(logging.DEBUG if debug else logging.WARNING)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.DEBUG if debug else logging.WARNING)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ecoflow-diag", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="list nearby EcoFlow stations")
    scan.add_argument("--timeout", type=float, default=10.0, help="scan time in seconds")

    probe = sub.add_parser("probe", help="connect, authenticate and print telemetry")
    probe.add_argument("--address", help="BLE address of the station (needed if several)")
    probe.add_argument("--scan-timeout", type=float, default=10.0)
    probe.add_argument("--connect-timeout", type=float, default=60.0)
    probe.add_argument("--attempts", type=int, default=3, help="BLE connection attempts")
    probe.add_argument("--duration", type=float, default=60.0, help="telemetry seconds")
    probe.add_argument("--interval", type=float, default=5.0, help="seconds between lines")
    probe.add_argument("--all-fields", action="store_true", help="dump all decoded fields")
    probe.add_argument(
        "--experimental-profile",
        metavar="MODULE",
        help="force an upstream device module (UNVERIFIED decoding)",
    )
    monitor = monitor_cli.add_parser(sub)
    for p in (scan, probe, monitor):
        p.add_argument("--debug", action="store_true", help="verbose, redacted logging")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.debug)
    commands = {
        "scan": lambda: cmd_scan(args),
        "probe": lambda: cmd_probe(args),
        "monitor": lambda: monitor_cli.cmd_monitor(args, redactor, read_user_id),
    }
    try:
        return asyncio.run(commands[args.command]())
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
