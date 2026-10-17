"""The EcoFlow user ID, stored encrypted with Windows DPAPI for the current user.

DPAPI ties the ciphertext to the Windows account: another user, or the file copied to another
machine, cannot decrypt it. The user ID never goes into settings.json or the logs.
"""

import ctypes
import logging
import sys
from ctypes import wintypes
from pathlib import Path

log = logging.getLogger(__name__)

# Binds the ciphertext to this purpose, so other DPAPI blobs of the user cannot be swapped in
_ENTROPY = b"EcoFlowCompanion/user-id/v1"
_CRYPTPROTECT_UI_FORBIDDEN = 0x01


class _Blob(ctypes.Structure):
    _fields_ = (("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char)))


def _dpapi(protect: bool, data: bytes) -> bytes:
    if sys.platform != "win32":
        raise OSError("DPAPI is only available on Windows")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    func = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    blob_p = ctypes.POINTER(_Blob)
    func.argtypes = (
        blob_p, wintypes.LPVOID, blob_p, wintypes.LPVOID, wintypes.LPVOID, wintypes.DWORD, blob_p,
    )  # fmt: skip
    func.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = (wintypes.HLOCAL,)
    kernel32.LocalFree.restype = wintypes.HLOCAL

    data_buf = ctypes.create_string_buffer(data, len(data))
    entropy_buf = ctypes.create_string_buffer(_ENTROPY, len(_ENTROPY))
    data_in = _Blob(len(data), ctypes.cast(data_buf, ctypes.POINTER(ctypes.c_char)))
    entropy = _Blob(len(_ENTROPY), ctypes.cast(entropy_buf, ctypes.POINTER(ctypes.c_char)))
    out = _Blob()
    ok = func(
        ctypes.byref(data_in), None, ctypes.byref(entropy), None, None,
        _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out),
    )  # fmt: skip
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(out.pbData, wintypes.HLOCAL))


def protect(data: bytes) -> bytes:
    return _dpapi(True, data)


def unprotect(data: bytes) -> bytes:
    return _dpapi(False, data)


def is_valid_user_id(value: str) -> bool:
    return value.isascii() and value.isdigit() and 1 <= len(value) <= 32


class CredentialStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> str | None:
        try:
            blob = self._path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError:
            log.warning("Cannot read the stored user ID")
            return None
        try:
            value = unprotect(blob).decode("ascii")
        except (OSError, UnicodeDecodeError):
            log.warning("Stored user ID cannot be decrypted (other Windows user or damaged)")
            return None
        return value if is_valid_user_id(value) else None

    def save(self, user_id: str) -> None:
        if not is_valid_user_id(user_id):
            raise ValueError("the EcoFlow user ID must be numeric")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_bytes(protect(user_id.encode("ascii")))
        tmp.replace(self._path)

    def clear(self) -> None:
        self._path.unlink(missing_ok=True)
