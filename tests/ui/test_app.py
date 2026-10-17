"""The application controller end to end, with the simulated station and temporary storage."""

import json
import threading
from dataclasses import replace

import pytest
import shiboken6
from PySide6.QtCore import QObject, QPoint, QUuid

from ecoflow_companion.app import AppController
from ecoflow_companion.application.event_rules import EventRules
from ecoflow_companion.application.monitor_service import MonitorConfig
from ecoflow_companion.application.ports import StationSelector
from ecoflow_companion.application.reconnect_policy import ReconnectPolicy
from ecoflow_companion.config import Settings
from ecoflow_companion.infrastructure.ecoflow.mock_adapter import MockScenario, MockStationAdapter
from ecoflow_companion.infrastructure.storage.paths import AppPaths
from ecoflow_companion.infrastructure.storage.settings_repository import SettingsRepository
from ecoflow_companion.infrastructure.windows.autostart import VALUE_NAME, Autostart
from ecoflow_companion.infrastructure.windows.credentials import CredentialStore
from ecoflow_companion.presentation.service_host import ServiceHost
from ecoflow_companion.presentation.settings_dialog import SettingsResult
from ecoflow_companion.presentation.single_instance import SingleInstance
from ecoflow_companion.redaction import Redactor

FAST = MockScenario(update_interval=0.02, link_drop_every=None)


class FakeRunKey:
    def __init__(self):
        self.values: dict[str, str] = {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name):
        self.values.pop(name, None)


@pytest.fixture
def controller(qapp, tmp_path, monkeypatch):
    monkeypatch.delenv("ECOFLOW_USER_ID", raising=False)
    paths = AppPaths(config_dir=tmp_path / "cfg", log_dir=tmp_path / "logs")
    run_key = FakeRunKey()
    c = AppController(
        paths=paths,
        settings_repo=SettingsRepository(paths.settings_file),
        credentials=CredentialStore(paths.credentials_file),
        autostart=Autostart(run_key, command="cmd"),
        adapter=MockStationAdapter(FAST),
        redactor=Redactor(),
        mock=True,
    )
    c.run_key = run_key  # type: ignore[attr-defined]
    c.settings_file = paths.settings_file  # type: ignore[attr-defined]
    yield c
    c.shutdown()
    # Signal connections tie the controller and its Qt objects into reference cycles, and the
    # cyclic GC frees those on whatever thread it runs: on the next test's service thread,
    # deleting widgets aborted the process. Delete them here, on the GUI thread.
    for obj in (c.widget, c.tray.menu, c):
        shiboken6.delete(obj)


def test_shows_live_data_from_the_service(controller, pump):
    controller.start()

    pump(lambda: controller.widget.battery.text().endswith("%"))

    assert controller.widget.isVisible()
    assert "Connected" in controller.widget.link.text()
    assert controller.widget.badge.text() == "SIMULATED"
    assert "Simulated station" in controller.tray.tray.toolTip()


def test_widget_position_and_visibility_are_saved(controller, pump):
    controller.start()

    controller.widget.moved.emit(QPoint(123, 456))
    controller.hide_widget()
    file = controller.settings_file
    pump(lambda: file.exists() and json.loads(file.read_text())["widget"]["x"] == 123)

    saved = json.loads(file.read_text())["widget"]
    assert (saved["x"], saved["y"], saved["visible"]) == (123, 456, False)
    assert not controller.widget.isVisible()
    assert controller.tray.menu.actions()[0].text() == "Show widget"


def test_close_hides_to_tray_by_default(controller):
    controller.start()

    controller.widget.close()

    assert not controller.widget.isVisible()
    assert controller.host.running


def test_apply_settings(controller, pump):
    controller.start()
    new = replace(
        Settings(),
        notifications=replace(Settings().notifications, battery_thresholds=(40, 15)),
        widget=replace(Settings().widget, always_on_top=True),
        startup=replace(Settings().startup, launch_at_login=True),
    )

    controller.apply_settings(SettingsResult(new, user_id=None))

    assert controller.settings.notifications.battery_thresholds == (40, 15)
    assert controller.widget.always_on_top
    assert controller.run_key.values[VALUE_NAME] == "cmd"  # type: ignore[attr-defined]
    saved = json.loads(controller.settings_file.read_text())
    assert saved["notifications"]["battery_thresholds"] == [40, 15]


def test_shutdown_stops_the_service(controller, pump):
    controller.start()
    pump(lambda: "Connected" in controller.widget.link.text())

    controller.shutdown()

    assert not controller.host.running
    assert not controller.widget.isVisible()


def test_events_reach_the_gui_thread(qapp, pump):
    host = ServiceHost(MockStationAdapter(FAST))
    threads = []

    class Receiver(QObject):  # lives on the GUI thread
        def on_event(self, event):
            threads.append(threading.current_thread())

    receiver = Receiver()
    host.event_received.connect(receiver.on_event)
    host.start(
        StationSelector(),
        lambda: "mock",
        MonitorConfig(tick_seconds=0.05),
        EventRules(),
        ReconnectPolicy(initial_delay=0.01, max_delay=0.02),
    )
    try:
        pump(lambda: len(threads) >= 3)
    finally:
        assert host.stop(5)

    assert set(threads) == {threading.main_thread()}


def test_scan_through_the_host(qapp, pump):
    host = ServiceHost(MockStationAdapter(FAST))
    results = []
    host.scan_finished.connect(lambda stations, error: results.append((stations, error)))
    host.start(StationSelector(), lambda: "mock", MonitorConfig(), EventRules(), ReconnectPolicy())
    try:
        host.scan(0.01)
        pump(lambda: results)
    finally:
        host.stop(5)

    stations, error = results[0]
    assert error is None and stations[0].model == "Simulated station"


def test_second_instance_activates_the_first(qapp, pump):
    name = f"ecoflow-test-{QUuid.createUuid().toString()[1:9]}"
    first = SingleInstance(name)
    activations = []
    first.activated.connect(lambda: activations.append(1))

    assert first.acquire()
    assert not SingleInstance(name).acquire()
    pump(lambda: activations)


def test_connection_changes_are_logged(controller, pump, caplog):
    caplog.set_level("INFO")
    controller.start()

    pump(lambda: "Connection: connected" in caplog.text)


class RecordingNotifier:
    def __init__(self):
        self.sent = []

    def notify(self, notification):
        from ecoflow_companion.application.notification_service import DeliveryResult

        self.sent.append(notification)
        return DeliveryResult(True, "recorded")


def test_alerts_become_notifications_with_context(controller, pump):
    from datetime import UTC, datetime

    from ecoflow_companion.domain.events import GridLost

    notifier = RecordingNotifier()
    controller.notifications._notifier = notifier
    controller.start()
    pump(lambda: controller.widget.battery.text().endswith("%"))

    controller.on_event(GridLost(datetime(2026, 10, 17, 10, 0, tzinfo=UTC)))

    assert [n.title for n in notifier.sent] == ["Grid power lost"]
    assert "Simulated station is running on battery" in notifier.sent[0].body
    assert "Battery" in notifier.sent[0].body


def test_settings_toggle_reaches_notifications(controller):
    from datetime import UTC, datetime

    from ecoflow_companion.domain.events import GridLost

    notifier = RecordingNotifier()
    controller.notifications._notifier = notifier
    controller.start()
    off = replace(Settings(), notifications=replace(Settings().notifications, grid_state=False))

    controller.apply_settings(SettingsResult(off, user_id=None))
    controller.on_event(GridLost(datetime(2026, 10, 17, 10, 0, tzinfo=UTC)))

    assert notifier.sent == []


def test_tray_fallback_reports_unavailable_tray(qapp):
    from PySide6.QtWidgets import QSystemTrayIcon

    from ecoflow_companion.application.notification_service import Notification
    from ecoflow_companion.presentation.tray import TrayBalloonNotifier

    result = TrayBalloonNotifier(QSystemTrayIcon()).notify(Notification("x", "t", "b"))

    assert not result.accepted
