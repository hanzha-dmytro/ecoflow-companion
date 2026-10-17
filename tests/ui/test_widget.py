from datetime import UTC, datetime

from PySide6.QtCore import QPoint, QPointF, QRect, QSize, Qt
from PySide6.QtGui import QMouseEvent

from ecoflow_companion.domain.models import (
    ConnectionState,
    FailureKind,
    LinkStatus,
    ModelSupport,
    StationInfo,
    StationSnapshot,
)
from ecoflow_companion.presentation.settings_dialog import (
    ANY_STATION,
    SettingsDialog,
    parse_thresholds,
)
from ecoflow_companion.presentation.view_models import Tone, build_view
from ecoflow_companion.presentation.widget import DesktopWidget, GridPlug, place_on_screen

AT = datetime(2026, 10, 17, 9, 44, 48, tzinfo=UTC)
STATION = StationInfo(
    "AA:BB:CC:DD:57:2E", "P321ZZZZ12345678", "Delta 3 Classic", ModelSupport.TESTED
)


def test_render_live_and_stale(qapp):
    widget = DesktopWidget()

    widget.render(
        build_view(
            StationSnapshot(54, 310.0, 118.0, True, AT, stale=False),
            ConnectionState(LinkStatus.CONNECTED),
            STATION,
        )
    )
    assert widget.battery.text() == "54%"
    assert widget.ac_in.text() == "AC in  310 W"
    assert (widget.grid.tone, widget.grid.toolTip()) == (Tone.OK, "Grid connected")
    assert widget.toolTip() == ""  # the card shows it all; only the tray needs the summary
    assert widget.footer.isHidden()

    widget.render(
        build_view(
            StationSnapshot(54, 310.0, 118.0, True, AT, stale=True),
            ConnectionState(LinkStatus.RECONNECTING, AT, FailureKind.BUSY, "x"),
            STATION,
        )
    )
    assert "Reconnecting" in widget.link.text()
    assert not widget.footer.isHidden()
    assert "Station busy" in widget.footer.text()


def test_grid_plug_paints_every_state(qapp):
    plug = GridPlug()
    for tone in Tone:
        for charging in (False, True):
            plug.set_state(f"Grid {tone}", tone, charging)
            assert not plug.grab().isNull()  # paintEvent ran
            assert plug.accessibleName() == f"Grid {tone}"


def test_menu_actions_emit_signals(qapp):
    widget = DesktopWidget()
    seen = []
    widget.settings_requested.connect(lambda: seen.append("settings"))
    widget.reconnect_requested.connect(lambda: seen.append("reconnect"))
    widget.exit_requested.connect(lambda: seen.append("exit"))
    widget.always_on_top_toggled.connect(lambda on: seen.append(f"top={on}"))

    actions = {a.text(): a for a in widget.build_menu().actions() if a.text()}
    for name in ("Settings…", "Reconnect", "Always on top", "Exit"):
        actions[name].trigger()

    assert seen == ["settings", "reconnect", "top=True", "exit"]


def test_close_is_delegated_not_performed(qapp):
    widget = DesktopWidget()
    widget.show()
    closes = []
    widget.close_requested.connect(lambda: closes.append(1))

    widget.close()

    assert closes == [1]
    assert widget.isVisible()
    widget.hide()


def test_drag_moves_and_reports_position(qapp):
    widget = DesktopWidget()
    widget.move(100, 100)
    widget.show()
    moved = []
    widget.moved.connect(moved.append)

    def mouse(kind, local, global_, buttons):
        return QMouseEvent(
            kind, QPointF(local), QPointF(global_), Qt.MouseButton.LeftButton, buttons,
            Qt.KeyboardModifier.NoModifier,
        )  # fmt: skip

    left = Qt.MouseButton.LeftButton
    none = Qt.MouseButton.NoButton
    widget.mousePressEvent(
        mouse(QMouseEvent.Type.MouseButtonPress, QPoint(10, 10), QPoint(110, 110), left)
    )
    widget.mouseMoveEvent(mouse(QMouseEvent.Type.MouseMove, QPoint(10, 10), QPoint(160, 140), left))
    widget.mouseReleaseEvent(
        mouse(QMouseEvent.Type.MouseButtonRelease, QPoint(10, 10), QPoint(160, 140), none)
    )

    assert widget.pos() == QPoint(150, 130)
    assert moved == [QPoint(150, 130)]
    widget.hide()


def test_always_on_top_keeps_widget_visible(qapp):
    widget = DesktopWidget()
    widget.show()

    widget.set_always_on_top(True)

    assert widget.always_on_top
    assert widget.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert widget.isVisible()
    widget.hide()


def test_place_on_screen():
    size = QSize(270, 160)
    screens = [QRect(0, 0, 1920, 1040)]

    assert place_on_screen(QPoint(100, 200), size, screens) == QPoint(100, 200)
    # Saved on a monitor that is gone: back to the bottom-right corner of the first screen
    default = place_on_screen(QPoint(3000, 200), size, screens)
    assert default == QPoint(1920 - 270 - 16, 1040 - 160 - 16)
    assert place_on_screen(None, size, screens) == default
    # Title strip barely on screen is not enough to grab it
    assert place_on_screen(QPoint(-250, 100), size, screens) == default
    # Second monitor to the left
    assert place_on_screen(QPoint(-1500, 300), size, [*screens, QRect(-1920, 0, 1920, 1080)]) == (
        QPoint(-1500, 300)
    )


def test_parse_thresholds():
    assert parse_thresholds("30, 20, 10") == (30, 20, 10)
    assert parse_thresholds("10;30") == (30, 10)
    assert parse_thresholds("15") == (15,)
    for bad in ("", "abc", "0", "100", "1,2,3,4,5,6"):
        assert parse_thresholds(bad) is None


def test_dialog_produces_settings(qapp):
    from ecoflow_companion.config import Settings

    dialog = SettingsDialog(Settings(), has_user_id=False)
    dialog.thresholds.setText("25, 15")
    dialog.user_id.setText("123456")
    dialog.launch_at_login.setChecked(True)
    dialog.show_scan_results([STATION], None)
    dialog.station.setCurrentIndex(1)

    dialog.accept()
    result = dialog.settings_result()

    assert result is not None
    assert result.user_id == "123456"
    assert result.settings.notifications.battery_thresholds == (25, 15)
    assert result.settings.startup.launch_at_login is True
    assert result.settings.device.preferred_address == STATION.address
    assert result.settings.device.preferred_serial == STATION.serial


def test_dialog_rejects_invalid_input(qapp):
    from ecoflow_companion.config import Settings

    dialog = SettingsDialog(Settings(), has_user_id=True)
    dialog.thresholds.setText("0")

    dialog.accept()

    assert dialog.settings_result() is None
    assert not dialog.error.isHidden()
    assert dialog.station.itemText(0) == ANY_STATION
