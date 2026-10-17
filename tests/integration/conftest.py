import pytest
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

# Made-up serial with the DELTA 3 Classic prefix
SERIAL = "P321ZZZZ12345678"


@pytest.fixture
def advertised_station() -> tuple[BLEDevice, AdvertisementData]:
    """A DELTA 3 Classic as bleak would report it; manufacturer data carries the serial."""
    ble = BLEDevice("AA:BB:CC:DD:57:2E", "EF-P32E2666", details=None)
    adv = AdvertisementData(
        local_name="EF-P32E2666",
        manufacturer_data={0xB5B5: b"\x13" + SERIAL.encode() + b"\x00" * 8},
        service_data={},
        service_uuids=[],
        tx_power=None,
        rssi=-56,
        platform_data=(),
    )
    return ble, adv
