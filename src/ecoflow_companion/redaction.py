"""Masking of identifying values (BLE address, serial number, user ID) in log output.

The vendored upstream library names its loggers after the BLE address and can include the
serial number in messages, so redaction is applied to the fully formatted log line rather
than to individual arguments.
"""

import hashlib
import logging
import re
import threading

_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2})[:\-_]?" + r"([0-9A-Fa-f]{2})[:\-_]?" * 5 + "$")


def mask_address(address: str) -> str:
    """Keep only the last two octets of a BLE address: ``**:**:**:**:EE:FF``."""
    octets = re.split(r"[:\-_]", address)
    if len(octets) != 6:
        return "<address>"
    return ":".join(["**"] * 4 + [o.upper() for o in octets[4:]])


def mask_serial(serial: str) -> str:
    """Keep only the 4-character model prefix of a serial number."""
    return f"{serial[:4]}{'*' * max(len(serial) - 4, 4)}"


def mask_device_name(name: str) -> str:
    """Keep the model part of an advertised name (``EF-P3``); the rest echoes the serial."""
    return f"{name[:5]}{'*' * max(len(name) - 5, 4)}"


def auth_digest(user_id: str, serial: str) -> str:
    """The value upstream sends to authenticate (MD5 of user ID + serial, upper hex)."""
    return hashlib.md5((user_id + serial).encode("ascii")).hexdigest().upper()


class Redactor:
    """Thread-safe registry of sensitive strings and their masked replacements."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._replacements: dict[str, str] = {}
        self._pattern: re.Pattern[str] | None = None

    def add_address(self, address: str | None) -> None:
        if not address or not _MAC_RE.match(address):
            return
        masked = mask_address(address)
        octets = re.split(r"[:\-_]", address)
        with self._lock:
            # Upstream logs addresses with ':' and file names with '_'
            for sep in (":", "-", "_", ""):
                self._replacements[sep.join(octets).lower()] = masked
            self._rebuild()

    def add_serial(self, serial: str | None) -> None:
        if serial:
            self._add(serial, mask_serial(serial))

    def add_device_name(self, name: str | None) -> None:
        if name and len(name) > 5:
            self._add(name, mask_device_name(name))

    def add_secret(self, value: str | None) -> None:
        if value:
            self._add(value, "<redacted>")

    def add_credentials(self, user_id: str, serial: str) -> None:
        """Register the user ID and the auth digest derived from it."""
        self.add_secret(user_id)
        self.add_secret(auth_digest(user_id, serial))

    def apply(self, text: str) -> str:
        pattern = self._pattern
        if pattern is None:
            return text
        return pattern.sub(lambda m: self._replacements.get(m.group(0).lower(), "<redacted>"), text)

    def _add(self, value: str, replacement: str) -> None:
        with self._lock:
            self._replacements[value.lower()] = replacement
            self._rebuild()

    def _rebuild(self) -> None:
        # Longest first so a value is never partially replaced by a shorter one it contains
        keys = sorted(self._replacements, key=len, reverse=True)
        self._pattern = re.compile("|".join(re.escape(k) for k in keys), re.IGNORECASE)


class RedactingFormatter(logging.Formatter):
    """Formatter that masks every registered value in the final log line."""

    def __init__(self, redactor: Redactor, fmt: str | None = None, datefmt: str | None = None):
        super().__init__(fmt, datefmt)
        self._redactor = redactor

    def format(self, record: logging.LogRecord) -> str:
        return self._redactor.apply(super().format(record))
