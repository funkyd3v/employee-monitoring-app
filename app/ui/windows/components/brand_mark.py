"""Brand mark: the application logo glyph, painted programmatically.

No binary assets exist yet (``assets/`` ships empty ``.gitkeep`` files), so
the brand is drawn with ``QPainter`` from theme tokens. Used on the title bar
and the login/center screens at different sizes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from app.config.constants import APP_NAME_SHORT
from app.ui.theme.tokens import ACTIVE_PALETTE

if TYPE_CHECKING:
    from PySide6.QtGui import QPaintEvent

_INITIALS = APP_NAME_SHORT[:2]


class BrandMark(QWidget):
    """A rounded, gradient-filled square with the brand initials."""

    def __init__(
        self,
        size: int = 24,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt override)
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect().adjusted(0, 0, -1, -1)

        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0.0, QColor(ACTIVE_PALETTE.accent))
        gradient.setColorAt(1.0, QColor(ACTIVE_PALETTE.accent_end))

        path = QPainterPath()
        path.addRoundedRect(rect, rect.height() / 3, rect.height() / 3)
        painter.fillPath(path, gradient)

        painter.setPen(
            QPen(QColor(ACTIVE_PALETTE.accent_end).darker(140), 1, Qt.PenStyle.SolidLine)
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        font = QFont("Inter", max(7, self._size // 4), QFont.Weight.DemiBold)
        font.setPixelSize(int(self._size * 0.38))
        painter.setFont(font)
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(
            self.rect(),
            Qt.AlignmentFlag.AlignCenter,
            _INITIALS,
        )
        painter.end()

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        return QSize(self._size, self._size)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        return QSize(self._size, self._size)
