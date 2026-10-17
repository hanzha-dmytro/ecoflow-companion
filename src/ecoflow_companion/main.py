"""Entry point of the desktop application.

ecoflow-companion            (no console window)
python -m ecoflow_companion [--mock] [--debug]
"""

import argparse
import logging
import sys

from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from ecoflow_companion.app import AppController
from ecoflow_companion.application.notification_service import NotificationService, Notifier
from ecoflow_companion.application.ports import StationAdapter
from ecoflow_companion.infrastructure.ecoflow.eflib_adapter import EflibAdapter
from ecoflow_companion.infrastructure.ecoflow.mock_adapter import MockStationAdapter
from ecoflow_companion.infrastructure.storage.paths import AppPaths
from ecoflow_companion.infrastructure.storage.settings_repository import SettingsRepository
from ecoflow_companion.infrastructure.windows.autostart import Autostart
from ecoflow_companion.infrastructure.windows.clock import AwakeClock
from ecoflow_companion.infrastructure.windows.credentials import CredentialStore
from ecoflow_companion.infrastructure.windows.show_desktop import ShowDesktopGuard
from ecoflow_companion.infrastructure.windows.toast import WinRtToastNotifier, register_app_id
from ecoflow_companion.logging_setup import configure_logging
from ecoflow_companion.presentation.icons import app_icon
from ecoflow_companion.presentation.single_instance import SingleInstance
from ecoflow_companion.presentation.widget import DesktopWidget
from ecoflow_companion.redaction import Redactor

log = logging.getLogger("ecoflow_companion")


def toast_notifier(paths: AppPaths) -> Notifier | None:
    """Windows toasts if they can be set up; None makes the app fall back to the tray."""
    try:
        paths.icon_file.parent.mkdir(parents=True, exist_ok=True)
        icon = paths.icon_file if app_icon().pixmap(256).save(str(paths.icon_file)) else None
        register_app_id(icon)
        notifier = WinRtToastNotifier()
    except (ImportError, OSError) as e:
        log.warning("Windows toasts unavailable (%s); using tray notifications", e)
        return None
    log.info("Using Windows toast notifications")
    return notifier


def show_desktop_guard(widget: DesktopWidget) -> ShowDesktopGuard | None:
    """Keeps the widget visible on Show desktop (Win+D); None if the hook cannot be set."""
    try:
        return ShowDesktopGuard(lambda: int(widget.winId()) if widget.isVisible() else None)
    except OSError as e:
        log.warning("Show desktop will cover the widget (%s)", e)
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ecoflow-companion", description="EcoFlow Companion")
    parser.add_argument("--mock", action="store_true", help="use a simulated station")
    parser.add_argument("--debug", action="store_true", help="verbose logging, also to stderr")
    parser.add_argument(
        "--test-notification",
        action="store_true",
        help="send one test notification and exit (exit code 0 if Windows accepted it)",
    )
    args = parser.parse_args(argv)

    paths = AppPaths.default()
    redactor = Redactor()
    configure_logging(paths.log_file, redactor, args.debug)

    app = QApplication(sys.argv[:1])
    app.setApplicationName("EcoFlow Companion")
    app.setWindowIcon(app_icon())
    app.setQuitOnLastWindowClosed(False)

    if args.test_notification:
        # Works next to a running copy: it needs no BLE and no single-instance lock
        notifier = toast_notifier(paths)
        if notifier is None:
            return 1
        return 0 if NotificationService(notifier).send_test().accepted else 1

    instance = SingleInstance()
    if not instance.acquire():
        log.info("Already running; asked the running copy to show its widget")
        return 0
    if not QSystemTrayIcon.isSystemTrayAvailable():
        log.warning("No system tray available; only the widget will be shown")

    # Timeouts and alert timing count only time the PC is awake (see the clock module)
    clock = AwakeClock()
    adapter: StationAdapter = (
        MockStationAdapter(clock=clock)
        if args.mock
        else EflibAdapter(redactor, clock=clock, debug_logging=args.debug)
    )
    controller = AppController(
        paths=paths,
        settings_repo=SettingsRepository(paths.settings_file),
        credentials=CredentialStore(paths.credentials_file),
        autostart=Autostart(),
        adapter=adapter,
        redactor=redactor,
        notifier=toast_notifier(paths),
        clock=clock,
        mock=args.mock,
    )
    instance.activated.connect(controller.show_widget)
    controller.start()
    _desktop_guard = show_desktop_guard(controller.widget)  # the hook lives while it is held
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
