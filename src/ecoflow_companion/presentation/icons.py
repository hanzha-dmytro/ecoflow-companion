"""Colours and the battery icon, drawn in code (no image resources to package)."""

from functools import cache

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

from ecoflow_companion.presentation.view_models import Tone

TONE_COLORS = {
    Tone.OK: "#3FB950",
    Tone.WARN: "#D29922",
    Tone.CRITICAL: "#F85149",
    Tone.UNKNOWN: "#8B949E",
}
TEXT = "#E6EDF3"
TEXT_SECONDARY = "#9DA7B3"
CARD = QColor(22, 27, 34, 235)
OUTLINE = "#C9D1D9"


@cache
def battery_icon(level_bucket: int | None, tone: Tone, live: bool) -> QIcon:
    """Battery glyph; ``level_bucket`` is the level rounded to 10 % (None: unknown)."""
    size = 64
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    body = QRectF(4, 16, 50, 32)
    outline = QColor(OUTLINE if live else TONE_COLORS[Tone.UNKNOWN])
    painter.setPen(QPen(outline, 4))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(body, 6, 6)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(outline)
    painter.drawRoundedRect(QRectF(56, 26, 6, 12), 2, 2)

    if level_bucket is not None and live:
        inner = body.adjusted(6, 6, -6, -6)
        width = max(inner.width() * level_bucket / 100, 3)
        painter.setBrush(QColor(TONE_COLORS[tone]))
        painter.drawRect(QRectF(inner.left(), inner.top(), width, inner.height()))
    elif not live:
        # Disconnected or stale: a slash through the battery
        painter.setPen(QPen(QColor(TONE_COLORS[Tone.CRITICAL]), 5))
        painter.drawLine(14, 54, 46, 10)
    painter.end()
    return QIcon(pixmap)


def app_icon() -> QIcon:
    return battery_icon(80, Tone.OK, True)
