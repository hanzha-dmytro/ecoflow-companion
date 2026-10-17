"""Finding EcoFlow stations over BLE and identifying them with upstream's tables."""

import asyncio
import logging
from dataclasses import dataclass

from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak.exc import BleakError

from ecoflow_companion._vendor import eflib
from ecoflow_companion._vendor.eflib.device_mappings import ECOFLOW_DEVICE_LIST
from ecoflow_companion._vendor.eflib.devicebase import DeviceBase
from ecoflow_companion.application.ports import ConnectFailed, StationSelector
from ecoflow_companion.domain.models import FailureKind, ModelSupport, StationInfo
from ecoflow_companion.redaction import Redactor, mask_address, mask_serial

log = logging.getLogger(__name__)

# Upstream device modules this project has run on real hardware (docs/HARDWARE_LOG.md)
TESTED_MODULES = frozenset({"delta3_classic"})


@dataclass(frozen=True)
class Identification:
    serial: str
    upstream_class: type[DeviceBase] | None
    table_name: str | None
    table_packets: str | None

    @property
    def upstream_module(self) -> str | None:
        if self.upstream_class is None:
            return None
        return self.upstream_class.__module__.rsplit(".", 1)[-1]

    @property
    def upstream_model(self) -> str | None:
        if self.upstream_class is None:
            return None
        return (self.upstream_class.__doc__ or "").strip() or self.upstream_module

    @property
    def support(self) -> ModelSupport:
        if self.upstream_class is None:
            return ModelSupport.UNSUPPORTED
        if self.upstream_module in TESTED_MODULES:
            return ModelSupport.TESTED
        return ModelSupport.UPSTREAM_ONLY


def identify(serial: str) -> Identification:
    """Match a serial number against upstream device classes and the upstream name table."""
    table_entry = None
    # Same longest-prefix lookup as upstream's UnsupportedDevice
    for length in (5, 4, 3, 2):
        if (table_entry := ECOFLOW_DEVICE_LIST.get(serial[:length])) is not None:
            break
    return Identification(
        serial=serial,
        upstream_class=eflib.device_class_from_sn(serial),
        table_name=table_entry["name"] if table_entry else None,
        table_packets=table_entry["packets"] if table_entry else None,
    )


@dataclass(frozen=True)
class Found:
    ble_device: BLEDevice
    adv: AdvertisementData
    ident: Identification

    @property
    def info(self) -> StationInfo:
        return StationInfo(
            address=self.ble_device.address,
            serial=self.ident.serial,
            model=self.ident.upstream_model or self.ident.table_name,
            support=self.ident.support,
            rssi=self.adv.rssi,
        )


def found_from_advertisement(ble_device: BLEDevice, adv: AdvertisementData) -> Found | None:
    """A ``Found`` for an EcoFlow advertisement, or None for any other device."""
    raw_sn = eflib.sn_from_advertisement(adv)
    if not raw_sn:
        return None
    try:
        serial = raw_sn.decode("ascii")
    except UnicodeDecodeError:
        log.warning("Skipping EcoFlow advertisement with a non-ASCII serial")
        return None
    return Found(ble_device, adv, identify(serial))


def register(redactor: Redactor, item: Found) -> None:
    redactor.add_address(item.ble_device.address)
    redactor.add_serial(item.ident.serial)
    redactor.add_device_name(item.adv.local_name)


async def discover(scan_seconds: float, redactor: Redactor) -> list[Found]:
    """Scan for ``scan_seconds`` and return EcoFlow stations, strongest signal first."""
    results = await BleakScanner.discover(timeout=scan_seconds, return_adv=True)
    found = []
    for ble_device, adv in results.values():
        if (item := found_from_advertisement(ble_device, adv)) is not None:
            register(redactor, item)
            found.append(item)
    found.sort(key=lambda f: f.adv.rssi, reverse=True)
    return found


async def find(selector: StationSelector, scan_seconds: float, redactor: Redactor) -> Found:
    """Find the selected station, or the only one in range when the selector is empty.

    Raises ``ConnectFailed`` with NOT_FOUND, AMBIGUOUS or BLUETOOTH_UNAVAILABLE.
    """
    try:
        if selector.is_empty:
            return _only_station(await discover(scan_seconds, redactor))
        return await _scan_until(selector, scan_seconds, redactor)
    except (BleakError, OSError) as e:
        raise ConnectFailed(
            FailureKind.BLUETOOTH_UNAVAILABLE, redactor.apply(f"{type(e).__name__}: {e}")
        ) from e


def _only_station(found: list[Found]) -> Found:
    if not found:
        raise ConnectFailed(FailureKind.NOT_FOUND, "no EcoFlow station in range")
    if len(found) > 1:
        raise ConnectFailed(
            FailureKind.AMBIGUOUS, f"{len(found)} EcoFlow stations in range; select one"
        )
    return found[0]


async def _scan_until(selector: StationSelector, scan_seconds: float, redactor: Redactor) -> Found:
    loop = asyncio.get_running_loop()
    result: asyncio.Future[Found] = loop.create_future()

    def on_advertisement(ble_device: BLEDevice, adv: AdvertisementData) -> None:
        if result.done():
            return
        item = found_from_advertisement(ble_device, adv)
        if item is not None and selector.matches(item.info):
            result.set_result(item)

    async with BleakScanner(detection_callback=on_advertisement):
        try:
            async with asyncio.timeout(scan_seconds):
                item = await result
        except TimeoutError:
            wanted = (
                mask_address(selector.address)
                if selector.address
                else mask_serial(selector.serial or "")
            )
            raise ConnectFailed(
                FailureKind.NOT_FOUND, f"station {wanted} not seen within {scan_seconds:g} s"
            ) from None
    register(redactor, item)
    return item
