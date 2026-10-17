"""Keeps the desktop widget visible on Show desktop (Win+D or the taskbar's corner button).

Show desktop does not minimize the widget, which has no minimize box. Explorer raises the
desktop window over it instead, so a widget that is not always on top vanished behind the
wallpaper. Measured on Windows 11: after Win+D the widget is neither minimized nor hidden, just
below Progman; Win+M leaves it alone. The desktop window is Progman, or on older builds a
top-level WorkerW; either one holds the icon view (SHELLDLL_DefView).

A WinEvent hook watches the z-order of the top-level windows (EVENT_OBJECT_REORDER on the root
window). The foreground event is no use: it arrives a few milliseconds before Explorer raises
the desktop window, and Win+D with the desktop already in the foreground sends none. When the
desktop is the foreground window and above the widget, the widget is moved over it without
activation. ``HWND_TOP`` is silently ignored here (a background process may not raise a window
over the foreground one), so the widget is made topmost and at once non-topmost again, which
leaves it on top of the normal windows. The desktop keeps the foreground, and the next Win+D
brings the other windows back above the widget as before. A click on the wallpaper does not
raise the desktop window, so it leaves the widget where it is.
"""

import ctypes
import sys
from collections.abc import Callable
from ctypes import wintypes

_EVENT_OBJECT_REORDER = 0x8004
_OBJID_CLIENT = -4
_WINEVENT_OUTOFCONTEXT = 0x0000
_WINEVENT_SKIPOWNPROCESS = 0x0002
_GW_HWNDPREV = 3
_HWND_TOPMOST = -1
_HWND_NOTOPMOST = -2
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010
_DESKTOP_CLASSES = ("Progman", "WorkerW")

if sys.platform == "win32":
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _WinEventProc = ctypes.WINFUNCTYPE(
        None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND, wintypes.LONG, wintypes.LONG,
        wintypes.DWORD, wintypes.DWORD,
    )  # fmt: skip
    _user32.SetWinEventHook.argtypes = (
        wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, _WinEventProc, wintypes.DWORD,
        wintypes.DWORD, wintypes.DWORD,
    )  # fmt: skip
    _user32.SetWinEventHook.restype = wintypes.HANDLE
    _user32.GetDesktopWindow.argtypes = ()
    _user32.GetDesktopWindow.restype = wintypes.HWND
    _user32.GetForegroundWindow.argtypes = ()
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    _user32.GetClassNameW.restype = ctypes.c_int
    _user32.FindWindowExW.argtypes = (
        wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
    )  # fmt: skip
    _user32.FindWindowExW.restype = wintypes.HWND
    _user32.GetWindow.argtypes = (wintypes.HWND, wintypes.UINT)
    _user32.GetWindow.restype = wintypes.HWND
    _user32.SetWindowPos.argtypes = (
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.UINT,
    )  # fmt: skip
    _user32.SetWindowPos.restype = wintypes.BOOL


def _is_desktop(hwnd: int) -> bool:
    name = ctypes.create_unicode_buffer(16)
    _user32.GetClassNameW(hwnd, name, len(name))
    return name.value in _DESKTOP_CLASSES and bool(
        _user32.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None)
    )


def _is_above(upper: int, lower: int) -> bool:
    hwnd = _user32.GetWindow(lower, _GW_HWNDPREV)
    while hwnd:
        if hwnd == upper:
            return True
        hwnd = _user32.GetWindow(hwnd, _GW_HWNDPREV)
    return False


class ShowDesktopGuard:
    """Raises a window back over the desktop when Show desktop has covered it.

    ``window`` returns the window's HWND, or None while it is hidden. The hook is delivered
    through the message loop of the creating thread (the Qt GUI thread) and stays installed
    for as long as this object lives. Raises OSError if the hook cannot be installed.
    """

    def __init__(self, window: Callable[[], int | None]) -> None:
        if sys.platform != "win32":
            raise OSError("the Show desktop hook needs Windows")
        self._window = window
        self._root = _user32.GetDesktopWindow()
        self._proc = _WinEventProc(self._on_reorder)  # Windows calls it; keep it alive
        self._hook = _user32.SetWinEventHook(
            _EVENT_OBJECT_REORDER, _EVENT_OBJECT_REORDER, None, self._proc, 0, 0,
            _WINEVENT_OUTOFCONTEXT | _WINEVENT_SKIPOWNPROCESS,
        )  # fmt: skip
        if not self._hook:
            raise OSError("SetWinEventHook failed")

    def _on_reorder(self, hook, event, hwnd, id_object, id_child, thread, time) -> None:
        if hwnd != self._root or id_object != _OBJID_CLIENT:
            return  # not the top-level z-order
        desktop = _user32.GetForegroundWindow()
        if not desktop or not _is_desktop(desktop):
            return
        target = self._window()
        if target and _is_above(desktop, target):
            flags = _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE
            _user32.SetWindowPos(target, _HWND_TOPMOST, 0, 0, 0, 0, flags)
            _user32.SetWindowPos(target, _HWND_NOTOPMOST, 0, 0, 0, 0, flags)
