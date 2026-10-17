"""Per-user locations: settings roam with the profile, logs stay on the machine."""

import os
from dataclasses import dataclass
from pathlib import Path

APP_DIR_NAME = "EcoFlow Companion"


@dataclass(frozen=True, slots=True)
class AppPaths:
    config_dir: Path
    log_dir: Path

    @classmethod
    def default(cls) -> "AppPaths":
        home = Path.home()
        roaming = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
        local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
        return cls(config_dir=roaming / APP_DIR_NAME, log_dir=local / APP_DIR_NAME / "logs")

    @property
    def settings_file(self) -> Path:
        return self.config_dir / "settings.json"

    @property
    def credentials_file(self) -> Path:
        return self.config_dir / "user_id.dpapi"

    @property
    def log_file(self) -> Path:
        return self.log_dir / "companion.log"

    @property
    def icon_file(self) -> Path:
        """PNG that Windows shows on toasts; written at startup."""
        return self.log_dir.parent / "app.png"
