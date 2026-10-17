"""System tray icon and menu."""

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ecoflow_companion.application.notification_service import DeliveryResult, Notification
from ecoflow_companion.presentation.icons import battery_icon
from ecoflow_companion.presentation.view_models import StationView, Tone


def _bucket(level: int | None) -> int | None:
    return None if level is None else min(100, round(level / 10) * 10)


class TrayController(QObject):
    toggle_widget_requested = Signal()
    settings_requested = Signal()
    reconnect_requested = Signal()
    open_logs_requested = Signal()
    exit_requested = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._tray = QSystemTrayIcon(battery_icon(None, Tone.UNKNOWN, False), self)
        self._tray.setToolTip("EcoFlow Companion")
        self._menu = QMenu()
        self._toggle = self._menu.addAction("Hide widget", self.toggle_widget_requested.emit)
        self._menu.addAction("Reconnect", self.reconnect_requested.emit)
        self._menu.addAction("Settings…", self.settings_requested.emit)
        self._menu.addAction("Open logs", self.open_logs_requested.emit)
        self._menu.addSeparator()
        self._menu.addAction("Exit", self.exit_requested.emit)
        self._tray.setContextMenu(self._menu)
        self._tray.activated.connect(self._on_activated)

    @property
    def tray(self) -> QSystemTrayIcon:
        return self._tray

    @property
    def menu(self) -> QMenu:
        return self._menu

    def show(self) -> None:
        self._tray.show()

    def hide(self) -> None:
        self._tray.hide()

    def render(self, view: StationView) -> None:
        live = not view.stale
        self._tray.setIcon(battery_icon(_bucket(view.battery_level), view.battery_tone, live))
        self._tray.setToolTip(view.tooltip)

    def set_widget_visible(self, visible: bool) -> None:
        self._toggle.setText("Hide widget" if visible else "Show widget")

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_widget_requested.emit()


class TrayBalloonNotifier:
    """Fallback when toasts are unavailable: the tray icon's balloon, which Windows 10/11 shows
    as a toast. The API reports nothing back, so success means only "handed to Windows"."""

    def __init__(self, tray: QSystemTrayIcon) -> None:
        self._tray = tray

    def notify(self, notification: Notification) -> DeliveryResult:
        if not (self._tray.isVisible() and self._tray.supportsMessages()):
            return DeliveryResult(False, "tray icon not available")
        self._tray.showMessage(
            notification.title, notification.body, QSystemTrayIcon.MessageIcon.Information
        )
        return DeliveryResult(True, "handed to the tray (no delivery status from Windows)")
