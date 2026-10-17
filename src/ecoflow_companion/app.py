"""Application controller: wires settings, storage, the monitor service and the UI together."""

import logging
import os
from dataclasses import replace

from PySide6.QtCore import QObject, QPoint, Qt, QTimer, QUrl, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QApplication, QMessageBox

from ecoflow_companion.application.notification_service import (
    AlertContext,
    NotificationService,
    Notifier,
)
from ecoflow_companion.application.ports import Clock, StationAdapter, StationSelector
from ecoflow_companion.config import (
    DeviceSettings,
    Settings,
    monitor_config_from,
    notification_preferences_from,
    policy_from,
    rules_from,
    selector_from,
)
from ecoflow_companion.domain.events import (
    AlertEvent,
    BatteryLow,
    ConnectionChanged,
    DomainEvent,
    GridLost,
    GridRestored,
    LinkLost,
    LinkRestored,
    SnapshotUpdated,
    StationIdentified,
)
from ecoflow_companion.domain.models import (
    ConnectionState,
    ModelSupport,
    StationInfo,
    StationSnapshot,
)
from ecoflow_companion.infrastructure.storage.paths import AppPaths
from ecoflow_companion.infrastructure.storage.settings_repository import SettingsRepository
from ecoflow_companion.infrastructure.windows.autostart import Autostart
from ecoflow_companion.infrastructure.windows.credentials import CredentialStore
from ecoflow_companion.presentation.icons import app_icon
from ecoflow_companion.presentation.service_host import ServiceHost
from ecoflow_companion.presentation.settings_dialog import SettingsDialog, SettingsResult
from ecoflow_companion.presentation.tray import TrayBalloonNotifier, TrayController
from ecoflow_companion.presentation.view_models import build_view
from ecoflow_companion.presentation.widget import DesktopWidget, place_on_screen
from ecoflow_companion.redaction import Redactor

log = logging.getLogger(__name__)

USER_ID_ENV = "ECOFLOW_USER_ID"
MOCK_USER_ID = "mock"
SAVE_DELAY_MS = 500
STOP_TIMEOUT = 10.0


def describe_alert(event: AlertEvent) -> str:
    match event:
        case BatteryLow(percent, threshold, _, initial):
            start = " (already low at start)" if initial else ""
            return f"battery low: {percent}% (threshold {threshold}%){start}"
        case GridLost():
            return "grid power lost"
        case GridRestored():
            return "grid power restored"
        case LinkLost(at, reason):
            return f"BLE connection lost at {at.astimezone():%H:%M:%S} ({reason})"
        case LinkRestored(_, outage):
            return f"BLE connection restored after {outage.total_seconds():.0f} s"
    return type(event).__name__


class AppController(QObject):
    def __init__(
        self,
        *,
        paths: AppPaths,
        settings_repo: SettingsRepository,
        credentials: CredentialStore,
        autostart: Autostart,
        adapter: StationAdapter,
        redactor: Redactor,
        notifier: Notifier | None = None,
        clock: Clock | None = None,
        mock: bool = False,
    ) -> None:
        super().__init__()
        self._paths = paths
        self._repo = settings_repo
        self._credentials = credentials
        self._autostart = autostart
        self._redactor = redactor
        self._mock = mock
        self._clock = clock
        self._settings = settings_repo.load()
        self._user_id: str | None = None
        self._snapshot = StationSnapshot()
        self._connection = ConnectionState()
        self._station: StationInfo | None = None
        self._exiting = False
        self._dialog: SettingsDialog | None = None

        self.widget = DesktopWidget(self._settings.widget.always_on_top)
        self.tray = TrayController(self)
        self.host = ServiceHost(adapter, self)
        # Without a toast notifier (WinRT unavailable), fall back to the tray balloon
        self.notifications = NotificationService(
            notifier or TrayBalloonNotifier(self.tray.tray),
            notification_preferences_from(self._settings),
            clock=clock,
        )
        self._save_timer = QTimer(self, singleShot=True, interval=SAVE_DELAY_MS)
        self._save_timer.timeout.connect(self._save_settings)

    @property
    def settings(self) -> Settings:
        return self._settings

    # --- lifecycle -----------------------------------------------------------------------

    def start(self) -> None:
        self._user_id = self._load_user_id()
        self._connect_signals()

        size = self.widget.sizeHint()
        saved = self._settings.widget
        position = QPoint(saved.x, saved.y) if saved.x is not None and saved.y is not None else None
        screens = [s.availableGeometry() for s in QGuiApplication.screens()]
        self.widget.move(place_on_screen(position, size, screens))
        self._render()
        if self._settings.widget.visible:
            self.widget.show()
        self.tray.set_widget_visible(self._settings.widget.visible)
        self.tray.show()

        if self._settings.startup.launch_at_login:
            self._set_autostart(True)  # refresh the entry if this copy moved

        self.host.start(
            StationSelector() if self._mock else selector_from(self._settings),
            lambda: self._user_id,
            monitor_config_from(self._settings),
            rules_from(self._settings),
            policy_from(self._settings),
            self._clock,
        )
        log.info("Started%s", " with the simulated station" if self._mock else "")

    def _load_user_id(self) -> str | None:
        if self._mock:
            return MOCK_USER_ID
        user_id = os.environ.get(USER_ID_ENV, "").strip() or self._credentials.load()
        if user_id:
            self._redactor.add_secret(user_id)
        return user_id

    def _connect_signals(self) -> None:
        queued = Qt.ConnectionType.QueuedConnection
        self.host.event_received.connect(self.on_event, queued)
        self.host.scan_finished.connect(self._on_scan_finished, queued)

        w = self.widget
        w.settings_requested.connect(self.open_settings)
        w.reconnect_requested.connect(self.host.request_reconnect)
        w.open_logs_requested.connect(self.open_logs)
        w.always_on_top_toggled.connect(self._set_always_on_top)
        w.hide_requested.connect(self.hide_widget)
        w.exit_requested.connect(self.exit)
        w.close_requested.connect(self._on_widget_close)
        w.moved.connect(self._on_widget_moved)

        t = self.tray
        t.toggle_widget_requested.connect(self.toggle_widget)
        t.settings_requested.connect(self.open_settings)
        t.reconnect_requested.connect(self.host.request_reconnect)
        t.open_logs_requested.connect(self.open_logs)
        t.exit_requested.connect(self.exit)

        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.shutdown)

    def exit(self) -> None:
        self.shutdown()
        QApplication.quit()

    def shutdown(self) -> None:
        """Stop monitoring (disconnects) and save state. Safe to call more than once."""
        if self._exiting:
            return
        self._exiting = True
        log.info("Shutting down")
        self._save_timer.stop()
        self._save_settings()
        self.host.stop(STOP_TIMEOUT)
        self.tray.hide()
        self.widget.hide()

    # --- service events ------------------------------------------------------------------

    @Slot(object)
    def on_event(self, event: DomainEvent) -> None:
        match event:
            case SnapshotUpdated(snapshot):
                self._snapshot = snapshot
            case ConnectionChanged(state):
                self._connection = state
                log.info(
                    "Connection: %s%s",
                    state.status,
                    f" ({state.error}: {state.error_detail})" if state.error else "",
                )
            case StationIdentified(station):
                self._station = station
                self._pin_station(station)
            case _:
                log.info("Alert: %s", describe_alert(event))
                self.notifications.handle(event, self._alert_context())
        self._render()

    def _pin_station(self, station: StationInfo) -> None:
        if self._mock or station.support is ModelSupport.SIMULATED:
            return
        device = self._settings.device
        if device.preferred_address is None and device.preferred_serial is None:
            self._update(
                device=DeviceSettings(station.address, station.serial, station.model), now=True
            )
            log.info("Pinned the station for future connections")

    def _alert_context(self) -> AlertContext:
        snapshot = self._snapshot
        return AlertContext(
            station_name=self._station.model if self._station else None,
            battery_percent=None if snapshot.stale else snapshot.battery_percent,
        )

    def _render(self) -> None:
        view = build_view(
            self._snapshot,
            self._connection,
            self._station,
            thresholds=self._settings.notifications.battery_thresholds,
        )
        self.widget.render(view)
        self.tray.render(view)

    # --- widget and tray -----------------------------------------------------------------

    def show_widget(self) -> None:
        self.widget.show()
        self.widget.raise_()
        self.widget.activateWindow()
        self._set_widget_visible(True)

    def hide_widget(self) -> None:
        self.widget.hide()
        self._set_widget_visible(False)

    def toggle_widget(self) -> None:
        if self.widget.isVisible():
            self.hide_widget()
        else:
            self.show_widget()

    def _set_widget_visible(self, visible: bool) -> None:
        self.tray.set_widget_visible(visible)
        self._update(widget=replace(self._settings.widget, visible=visible))

    def _on_widget_close(self) -> None:
        if self._settings.widget.close_to_tray:
            self.hide_widget()
        else:
            self.exit()

    def _on_widget_moved(self, pos: QPoint) -> None:
        self._update(widget=replace(self._settings.widget, x=pos.x(), y=pos.y()))

    def _set_always_on_top(self, enabled: bool) -> None:
        self.widget.set_always_on_top(enabled)
        self._update(widget=replace(self._settings.widget, always_on_top=enabled))

    def open_logs(self) -> None:
        self._paths.log_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._paths.log_dir)))

    # --- settings ------------------------------------------------------------------------

    def open_settings(self) -> None:
        if self._dialog is not None:
            self._dialog.raise_()
            self._dialog.activateWindow()
            return
        dialog = SettingsDialog(self._settings, has_user_id=bool(self._user_id))
        dialog.setWindowIcon(app_icon())
        dialog.scan_requested.connect(lambda: self.host.scan(10.0))
        dialog.test_requested.connect(
            lambda: dialog.show_test_result(self.notifications.send_test())
        )
        dialog.finished.connect(lambda _code: self._on_dialog_finished(dialog))
        self._dialog = dialog
        dialog.show()

    def _on_scan_finished(self, stations: object, error: object) -> None:
        if self._dialog is not None:
            self._dialog.show_scan_results(stations, error)  # type: ignore[arg-type]

    def _on_dialog_finished(self, dialog: SettingsDialog) -> None:
        self._dialog = None
        result = dialog.settings_result()
        dialog.deleteLater()
        if result is not None:
            self.apply_settings(result)

    def apply_settings(self, result: SettingsResult) -> None:
        old = self._settings
        # The dialog does not edit the widget position or visibility; keep the live values
        new = replace(
            result.settings,
            widget=replace(
                result.settings.widget, x=old.widget.x, y=old.widget.y, visible=old.widget.visible
            ),
        )
        self._settings = new
        self._save_settings()

        reconnect = False
        if result.user_id and not self._mock:
            try:
                self._credentials.save(result.user_id)
            except (OSError, ValueError) as e:
                self._warn(f"The user ID could not be stored: {type(e).__name__}")
            self._user_id = result.user_id
            self._redactor.add_secret(result.user_id)
            reconnect = True

        if new.startup.launch_at_login != old.startup.launch_at_login:
            self._set_autostart(new.startup.launch_at_login)
        self.widget.set_always_on_top(new.widget.always_on_top)
        if (new.notifications, new.monitoring) != (old.notifications, old.monitoring):
            self.host.set_rules(rules_from(new))
            self.notifications.set_preferences(notification_preferences_from(new))
        if not self._mock and selector_from(new) != selector_from(old):
            self.host.set_selector(selector_from(new))
        elif reconnect:
            self.host.request_reconnect()
        self._render()

    def _set_autostart(self, enabled: bool) -> None:
        try:
            self._autostart.set_enabled(enabled)
        except OSError as e:
            log.warning("Could not change launch at login: %s", e)
            self._warn("Could not change 'Start with Windows'. See the log for details.")

    def _update(self, *, now: bool = False, **sections: object) -> None:
        self._settings = replace(self._settings, **sections)  # type: ignore[arg-type]
        if now:
            self._save_settings()
        else:
            self._save_timer.start()

    def _save_settings(self) -> None:
        try:
            self._repo.save(self._settings)
        except OSError:
            log.exception("Could not save settings")

    def _warn(self, text: str) -> None:
        QMessageBox.warning(None, "EcoFlow Companion", text)
