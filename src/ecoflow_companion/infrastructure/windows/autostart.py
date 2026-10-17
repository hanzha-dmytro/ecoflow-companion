"""Launch at Windows login through the per-user ``Run`` registry key (no admin rights)."""

import logging
import sys
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "EcoFlowCompanion"


def launch_command() -> str:
    """Command line that starts this installation without a console window."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    interpreter = pythonw if pythonw.exists() else Path(sys.executable)
    return f'"{interpreter}" -m ecoflow_companion'


class RunKey(Protocol):
    def get(self, name: str) -> str | None: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...


class WinRegRunKey:
    def get(self, name: str) -> str | None:
        import winreg  # Windows only; imported here so the module loads elsewhere

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                value, _ = winreg.QueryValueEx(key, name)
        except FileNotFoundError:
            return None
        return value if isinstance(value, str) else None

    def set(self, name: str, value: str) -> None:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)

    def delete(self, name: str) -> None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, name)
        except FileNotFoundError:
            pass


class Autostart:
    def __init__(self, run_key: RunKey | None = None, command: str | None = None) -> None:
        self._key = run_key or WinRegRunKey()
        self._command = command or launch_command()

    def is_enabled(self) -> bool:
        return self._key.get(VALUE_NAME) is not None

    def set_enabled(self, enabled: bool) -> None:
        """Add or remove the entry; an existing entry is rewritten if this copy moved."""
        if enabled:
            if self._key.get(VALUE_NAME) != self._command:
                self._key.set(VALUE_NAME, self._command)
                log.info("Launch at login enabled")
        elif self._key.get(VALUE_NAME) is not None:
            self._key.delete(VALUE_NAME)
            log.info("Launch at login disabled")
