"""The desktop widget: a small movable card with the station's state."""

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QColor,
    QContextMenuEvent,
    QFont,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QVBoxLayout, QWidget

from ecoflow_companion.presentation.icons import (
    CARD,
    TEXT,
    TEXT_SECONDARY,
    TONE_COLORS,
    app_icon,
)
from ecoflow_companion.presentation.view_models import StationView, Tone

WIDTH = 270
MARGIN = 16  # from the screen edge for the default position


def place_on_screen(saved: QPoint | None, size: QSize, screens: list[QRect]) -> QPoint:
    """The saved position if the widget would be reachable there, else bottom-right.

    Reachable: the title strip (top 40 px) lies on some screen. Monitors can be unplugged or
    rearranged between runs, and a widget placed off-screen could not be dragged back.
    """
    if saved is not None:
        grip = QRect(saved, QSize(size.width(), 40))
        if any(screen.intersected(grip).width() >= 40 for screen in screens):
            return saved
    area = screens[0] if screens else QRect(0, 0, 1280, 720)
    return QPoint(
        area.right() - size.width() - MARGIN + 1, area.bottom() - size.height() - MARGIN + 1
    )


class BatteryBar(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(8)
        self._level: int | None = None
        self._color = QColor(TONE_COLORS[Tone.UNKNOWN])

    def set_level(self, level: int | None, tone: Tone) -> None:
        self._level, self._color = level, QColor(TONE_COLORS[tone])
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 30))
        rect = QRectF(self.rect())
        painter.drawRoundedRect(rect, 4, 4)
        if self._level is not None:
            painter.setBrush(self._color)
            painter.drawRoundedRect(
                QRectF(rect.left(), rect.top(), rect.width() * self._level / 100, rect.height()),
                4,
                4,
            )


class GridPlug(QWidget):
    """A mains plug in the grid state's colour, crossed out when the grid is down.

    The cross keeps "down" readable without telling red from green. While the battery charges
    from the grid, a bolt is cut out of the plug's body.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(20, 26)  # the plug is drawn on this 20 x 26 grid
        self.tone = Tone.UNKNOWN
        self.charging = False

    def set_state(self, text: str, tone: Tone, charging: bool = False) -> None:
        self.tone, self.charging = tone, charging
        self.setToolTip(text)
        self.setAccessibleName(text)
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(TONE_COLORS[self.tone])
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(QRectF(4.5, 0, 3, 7), 1, 1)  # pins
        painter.drawRoundedRect(QRectF(12.5, 0, 3, 7), 1, 1)
        painter.drawRoundedRect(QRectF(1.5, 5, 17, 13), 3, 3)  # body
        painter.drawPolygon(QPolygonF([QPointF(5, 17.5), QPointF(15, 17.5), QPointF(12.5, 21.5),
                                       QPointF(7.5, 21.5)]))  # fmt: skip
        painter.drawRect(QRectF(8.75, 21, 2.5, 5))  # cable
        if self.charging:
            painter.setBrush(CARD)
            painter.drawPolygon(QPolygonF([QPointF(12.2, 6.2), QPointF(5.6, 12.7),
                                           QPointF(9.3, 12.7), QPointF(7.8, 17),
                                           QPointF(14.4, 10.3), QPointF(10.7, 10.3)]))  # fmt: skip
        if self.tone is Tone.CRITICAL:
            solid, round_cap = Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap
            for pen in (QPen(CARD, 4.5, solid, round_cap), QPen(color, 2, solid, round_cap)):
                painter.setPen(pen)  # a gap in the card colour, then the stroke
                painter.drawLine(QPointF(2, 24), QPointF(18, 2))


class DesktopWidget(QWidget):
    settings_requested = Signal()
    reconnect_requested = Signal()
    open_logs_requested = Signal()
    always_on_top_toggled = Signal(bool)
    hide_requested = Signal()
    exit_requested = Signal()
    close_requested = Signal()
    moved = Signal(QPoint)

    def __init__(self, always_on_top: bool = False) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("EcoFlow Companion")
        self.setWindowIcon(app_icon())
        self.setFixedWidth(WIDTH)
        self._always_on_top = False
        self.set_always_on_top(always_on_top)
        self._drag_offset: QPoint | None = None

        self.title = self._label(bold=True)
        self.badge = self._label(size=8, color=TONE_COLORS[Tone.WARN])
        self.link = self._label(size=9)
        self.battery = self._label(size=22, bold=True)
        self.bar = BatteryBar()
        self.ac_in = self._label()
        self.ac_out = self._label()
        self.grid = GridPlug()
        self.footer = self._label(size=8, color=TEXT_SECONDARY)
        self.footer.setWordWrap(True)

        header = QHBoxLayout()
        header.addWidget(self.title, 1)
        header.addWidget(self.link)
        level = QHBoxLayout()
        level.addWidget(self.battery, 1)
        level.addWidget(self.grid, 0, Qt.AlignmentFlag.AlignVCenter)
        power = QHBoxLayout()
        power.addWidget(self.ac_in)
        power.addStretch(1)
        power.addWidget(self.ac_out)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(4)
        layout.addLayout(header)
        layout.addWidget(self.badge)
        layout.addLayout(level)
        layout.addWidget(self.bar)
        layout.addLayout(power)
        layout.addWidget(self.footer)
        self.badge.hide()
        self.footer.hide()

    def _label(self, size: int = 10, bold: bool = False, color: str = TEXT) -> QLabel:
        label = QLabel()
        font = QFont(label.font())
        font.setPointSize(size)
        font.setBold(bold)
        label.setFont(font)
        label.setStyleSheet(f"color: {color}")
        return label

    @staticmethod
    def _paint(label: QLabel, text: str, color: str) -> None:
        label.setText(text)
        label.setStyleSheet(f"color: {color}")

    def render(self, view: StationView) -> None:
        value_color = TEXT_SECONDARY if view.stale else TEXT
        self._paint(self.title, view.title, TEXT)
        self.badge.setText(view.badge or "")
        self.badge.setVisible(view.badge is not None)
        self._paint(self.link, f"● {view.link_text}", TONE_COLORS[view.link_tone])
        battery_color = value_color if view.stale else TONE_COLORS[view.battery_tone]
        if view.battery_tone is Tone.OK and not view.stale:
            battery_color = TEXT
        self._paint(self.battery, view.battery_text, battery_color)
        self.bar.set_level(view.battery_level, view.battery_tone)
        self._paint(self.ac_in, f"AC in  {view.ac_in_text}", value_color)
        self._paint(self.ac_out, f"AC out  {view.ac_out_text}", value_color)
        self.grid.set_state(view.grid_text, view.grid_tone, view.charging)
        self.footer.setText("\n".join(view.footer))
        self.footer.setVisible(bool(view.footer))
        self.adjustSize()

    @property
    def always_on_top(self) -> bool:
        return self._always_on_top

    def set_always_on_top(self, enabled: bool) -> None:
        if enabled == self._always_on_top:
            return
        self._always_on_top = enabled
        visible = self.isVisible()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, enabled)
        if visible:  # changing window flags hides the window
            self.show()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor(255, 255, 255, 40))
        painter.setBrush(CARD)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.pos()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None and event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = None
            self.moved.emit(self.pos())
            event.accept()

    def build_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.addAction("Settings…", self.settings_requested.emit)
        menu.addAction("Reconnect", self.reconnect_requested.emit)
        menu.addAction("Open logs", self.open_logs_requested.emit)
        menu.addSeparator()
        on_top = QAction("Always on top", menu, checkable=True, checked=self._always_on_top)
        on_top.toggled.connect(self.always_on_top_toggled.emit)
        menu.addAction(on_top)
        menu.addAction("Hide widget", self.hide_requested.emit)
        menu.addSeparator()
        menu.addAction("Exit", self.exit_requested.emit)
        return menu

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        self.build_menu().exec(event.globalPos())

    def closeEvent(self, event: QCloseEvent) -> None:
        # Alt+F4 and similar: the controller decides between hiding and exiting
        event.ignore()
        self.close_requested.emit()
