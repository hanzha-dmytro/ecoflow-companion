"""Loading and saving ``Settings`` as JSON."""

import json
import logging
from datetime import datetime
from pathlib import Path

from ecoflow_companion.config import Settings

log = logging.getLogger(__name__)


class SettingsRepository:
    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> Settings:
        """Settings from disk; defaults if the file is missing or unreadable.

        An unreadable file is renamed (kept for troubleshooting) so the next save does not
        silently destroy it. Individually invalid values fall back to their defaults.
        """
        try:
            text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return Settings()
        except (OSError, UnicodeDecodeError) as e:
            log.warning("Cannot read settings (%s); using defaults", type(e).__name__)
            self._set_aside()
            return Settings()

        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if not isinstance(data, dict):
            log.warning("Settings file is not a JSON object; using defaults")
            self._set_aside()
            return Settings()

        settings, problems = Settings.from_dict(data)
        for problem in problems:
            log.warning("Settings: %s", problem)
        return settings

    def save(self, settings: Settings) -> None:
        """Write atomically: a crash mid-write leaves the previous file intact."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(json.dumps(settings.to_dict(), indent=2) + "\n", encoding="utf-8")
        tmp.replace(self._path)

    def _set_aside(self) -> None:
        stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
        target = self._path.with_name(f"{self._path.stem}.invalid-{stamp}{self._path.suffix}")
        try:
            self._path.replace(target)
            log.warning("Unreadable settings kept as %s", target.name)
        except OSError:
            log.exception("Could not set aside unreadable settings")
