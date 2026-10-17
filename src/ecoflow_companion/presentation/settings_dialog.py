"""Settings dialog. It edits a copy of ``Settings``; the controller applies the result."""

from dataclasses import dataclass, replace

from PySide6.QtCore import QRegularExpression, Signal
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ecoflow_companion.application.notification_service import DeliveryResult
from ecoflow_companion.config import DeviceSettings, Settings
from ecoflow_companion.domain.models import ModelSupport, StationInfo
from ecoflow_companion.infrastructure.windows.credentials import is_valid_user_id
from ecoflow_companion.presentation.icons import TONE_COLORS
from ecoflow_companion.presentation.view_models import SUPPORT_BADGE, Tone

ANY_STATION = "Any station in range (pinned on first connection)"


@dataclass(frozen=True, slots=True)
class SettingsResult:
    settings: Settings
    # A newly entered user ID; None keeps the stored one
    user_id: str | None


def parse_thresholds(text: str) -> tuple[int, ...] | None:
    """``"30, 20, 10"`` -> ``(30, 20, 10)``; None if invalid (1-5 values, each 1-99)."""
    try:
        values = {int(part) for part in text.replace(";", ",").split(",") if part.strip()}
    except ValueError:
        return None
    if not 1 <= len(values) <= 5 or not all(1 <= v <= 99 for v in values):
        return None
    return tuple(sorted(values, reverse=True))


def station_label(model: str | None, address: str, support: ModelSupport | None = None) -> str:
    badge = SUPPORT_BADGE.get(support) if support else None
    text = f"{model or 'EcoFlow station'} (…{address[-5:]})"
    return f"{text} [{badge}]" if badge else text


class SettingsDialog(QDialog):
    scan_requested = Signal()
    test_requested = Signal()

    def __init__(
        self, settings: Settings, has_user_id: bool, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("EcoFlow Companion settings")
        self._settings = settings
        self._result: SettingsResult | None = None

        # Station
        self.station = QComboBox()
        self.station.addItem(ANY_STATION, None)
        device = settings.device
        if device.preferred_address:
            self._add_station(
                device.preferred_name, device.preferred_address, device.preferred_serial
            )
            self.station.setCurrentIndex(1)
        self.scan_button = QPushButton("Scan")
        self.scan_button.clicked.connect(self._start_scan)
        self.scan_status = QLabel()
        self.scan_status.setWordWrap(True)
        station_row = QHBoxLayout()
        station_row.addWidget(self.station, 1)
        station_row.addWidget(self.scan_button)
        station_form = QFormLayout()
        station_form.addRow("Station", station_row)
        station_form.addRow("", self.scan_status)

        self.user_id = QLineEdit()
        self.user_id.setEchoMode(QLineEdit.EchoMode.Password)
        self.user_id.setValidator(QRegularExpressionValidator(QRegularExpression(r"\d{0,32}")))
        self.user_id.setPlaceholderText(
            "Stored — leave empty to keep" if has_user_id else "Numeric EcoFlow user ID"
        )
        show_id = QCheckBox("Show")
        show_id.toggled.connect(
            lambda on: self.user_id.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
            )
        )
        id_row = QHBoxLayout()
        id_row.addWidget(self.user_id, 1)
        id_row.addWidget(show_id)
        station_form.addRow("User ID", id_row)
        station_box = QGroupBox("Station")
        station_box.setLayout(station_form)

        # Alerts
        n = settings.notifications
        self.notifications_enabled = QCheckBox("Enable alerts", checked=n.enabled)
        self.thresholds = QLineEdit(", ".join(str(t) for t in n.battery_thresholds))
        self.hysteresis = QSpinBox(minimum=1, maximum=50, value=n.battery_hysteresis_percent)
        self.hysteresis.setSuffix(" %")
        self.grid_alerts = QCheckBox("Grid power lost / restored", checked=n.grid_state)
        self.ble_lost = QCheckBox("Bluetooth connection lost", checked=n.ble_disconnect)
        self.ble_restored = QCheckBox("Bluetooth connection restored", checked=n.ble_reconnect)
        self.test_button = QPushButton("Send test notification")
        self.test_button.clicked.connect(self.test_requested.emit)
        self.test_status = QLabel()
        self.test_status.setWordWrap(True)
        test_row = QHBoxLayout()
        test_row.addWidget(self.test_button)
        test_row.addWidget(self.test_status, 1)
        alerts_form = QFormLayout()
        alerts_form.addRow(self.notifications_enabled)
        alerts_form.addRow("Low battery at (%)", self.thresholds)
        alerts_form.addRow("Re-arm after rising by", self.hysteresis)
        alerts_form.addRow(self.grid_alerts)
        alerts_form.addRow(self.ble_lost)
        alerts_form.addRow(self.ble_restored)
        alerts_form.addRow(test_row)
        alerts_box = QGroupBox("Alerts")
        alerts_box.setLayout(alerts_form)

        # Widget and startup
        w = settings.widget
        self.always_on_top = QCheckBox("Keep the widget on top", checked=w.always_on_top)
        self.close_to_tray = QCheckBox(
            "Closing the widget keeps the app in the tray", checked=w.close_to_tray
        )
        self.launch_at_login = QCheckBox(
            "Start with Windows", checked=settings.startup.launch_at_login
        )
        general = QVBoxLayout()
        for box in (self.always_on_top, self.close_to_tray, self.launch_at_login):
            general.addWidget(box)
        general_box = QGroupBox("General")
        general_box.setLayout(general)

        self.error = QLabel()
        self.error.setStyleSheet(f"color: {TONE_COLORS[Tone.CRITICAL]}")
        self.error.hide()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        for part in (station_box, alerts_box, general_box, self.error, buttons):
            layout.addWidget(part)

    def _add_station(
        self, name: str | None, address: str, serial: str | None, support=None
    ) -> None:
        for i in range(self.station.count()):
            data = self.station.itemData(i)
            if data and data[0] == address:
                return
        self.station.addItem(station_label(name, address, support), (address, serial, name))

    def _start_scan(self) -> None:
        self.scan_button.setEnabled(False)
        self.scan_status.setText("Scanning… A station that is connected does not advertise.")
        self.scan_requested.emit()

    def show_test_result(self, result: DeliveryResult) -> None:
        tone = Tone.OK if result.accepted else Tone.CRITICAL
        text = "Sent: check the notification area." if result.accepted else "Not shown"
        self.test_status.setText(f"{text} ({result.detail})")
        self.test_status.setStyleSheet(f"color: {TONE_COLORS[tone]}")

    def show_scan_results(self, stations: list[StationInfo] | None, error: str | None) -> None:
        self.scan_button.setEnabled(True)
        if stations is None:
            self.scan_status.setText(f"Scan failed: {error}")
            return
        for station in stations:
            self._add_station(station.model, station.address, station.serial, station.support)
        found = len(stations)
        self.scan_status.setText(
            f"Found {found} station{'s' if found != 1 else ''}." if found else "No stations found."
        )

    def settings_result(self) -> SettingsResult | None:
        """What the user accepted; None if the dialog was cancelled."""
        return self._result

    def accept(self) -> None:
        thresholds = parse_thresholds(self.thresholds.text())
        if thresholds is None:
            return self._fail(
                "Battery thresholds: 1 to 5 numbers between 1 and 99, e.g. 30, 20, 10"
            )
        user_id = self.user_id.text().strip() or None
        if user_id is not None and not is_valid_user_id(user_id):
            return self._fail("The user ID must be numeric")

        selected = self.station.currentData()
        device = (
            DeviceSettings(
                preferred_address=selected[0],
                preferred_serial=selected[1],
                preferred_name=selected[2],
            )
            if selected
            else DeviceSettings()
        )
        s = self._settings
        settings = replace(
            s,
            device=device,
            notifications=replace(
                s.notifications,
                enabled=self.notifications_enabled.isChecked(),
                battery_thresholds=thresholds,
                battery_hysteresis_percent=self.hysteresis.value(),
                grid_state=self.grid_alerts.isChecked(),
                ble_disconnect=self.ble_lost.isChecked(),
                ble_reconnect=self.ble_restored.isChecked(),
            ),
            widget=replace(
                s.widget,
                always_on_top=self.always_on_top.isChecked(),
                close_to_tray=self.close_to_tray.isChecked(),
            ),
            startup=replace(s.startup, launch_at_login=self.launch_at_login.isChecked()),
        )
        self._result = SettingsResult(settings, user_id)
        super().accept()
        return None

    def _fail(self, message: str) -> None:
        self.error.setText(message)
        self.error.show()
