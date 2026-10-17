import sys

import pytest

from ecoflow_companion.infrastructure.windows.autostart import VALUE_NAME, Autostart
from ecoflow_companion.infrastructure.windows.credentials import (
    CredentialStore,
    is_valid_user_id,
    protect,
    unprotect,
)

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")
USER_ID = "1234567890123456789"


@windows_only
def test_dpapi_round_trip_and_tamper_detection():
    blob = protect(b"secret")

    assert b"secret" not in blob
    assert unprotect(blob) == b"secret"
    with pytest.raises(OSError):
        unprotect(blob[:-4] + b"xxxx")


@windows_only
def test_credential_store(tmp_path):
    path = tmp_path / "cfg" / "user_id.dpapi"
    store = CredentialStore(path)
    assert store.load() is None

    store.save(USER_ID)

    assert USER_ID.encode() not in path.read_bytes()
    assert store.load() == USER_ID
    store.clear()
    assert store.load() is None
    store.clear()  # idempotent


@windows_only
def test_damaged_credentials_read_as_missing(tmp_path):
    path = tmp_path / "user_id.dpapi"
    path.write_bytes(b"not a dpapi blob")

    assert CredentialStore(path).load() is None


def test_user_id_validation(tmp_path):
    assert is_valid_user_id(USER_ID)
    assert not is_valid_user_id("")
    assert not is_valid_user_id("12a")
    assert not is_valid_user_id("١٢٣")  # non-ASCII digits
    with pytest.raises(ValueError):
        CredentialStore(tmp_path / "x").save("abc")


class FakeRunKey:
    def __init__(self):
        self.values: dict[str, str] = {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name):
        self.values.pop(name, None)


def test_autostart_enable_disable():
    key = FakeRunKey()
    autostart = Autostart(key, command='"C:\\app\\pythonw.exe" -m ecoflow_companion')

    assert not autostart.is_enabled()
    autostart.set_enabled(True)
    assert key.values[VALUE_NAME] == '"C:\\app\\pythonw.exe" -m ecoflow_companion'
    assert autostart.is_enabled()
    autostart.set_enabled(False)
    assert not autostart.is_enabled()
    autostart.set_enabled(False)  # idempotent


def test_autostart_rewrites_entry_of_a_moved_copy():
    key = FakeRunKey()
    key.values[VALUE_NAME] = '"C:\\old\\pythonw.exe" -m ecoflow_companion'

    Autostart(key, command='"D:\\new\\pythonw.exe" -m ecoflow_companion').set_enabled(True)

    assert key.values[VALUE_NAME].startswith('"D:\\new')


def test_awake_clock_is_monotonic_and_tracks_time_while_awake():
    import time

    from ecoflow_companion.infrastructure.windows.clock import AwakeClock

    clock = AwakeClock()
    start, wall = clock.monotonic(), time.monotonic()
    readings = [clock.monotonic() for _ in range(1000)]
    time.sleep(0.2)
    elapsed, wall_elapsed = clock.monotonic() - start, time.monotonic() - wall

    assert readings == sorted(readings)
    # 15.6 ms resolution of the interrupt-time clock
    assert abs(elapsed - wall_elapsed) < 0.05
    assert clock.now().tzinfo is not None
