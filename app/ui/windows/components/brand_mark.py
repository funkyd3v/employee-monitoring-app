"""Brand mark: the application logo glyph, painted programmatically.

No binary assets exist yet (``assets/`` ships empty ``.gitkeep`` files), so
the brand is drawn with ``QPainter`` from theme tokens. Used on the title bar
and the login/center screens at different sizes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from app.config.constants import APP_NAME_SHORT
from app.ui.theme import ui_font
from app.ui.theme.tokens import ACTIVE_PALETTE

if TYPE_CHECKING:
    from PySide6.QtGui import QPaintEvent

_INITIALS = APP_NAME_SHORT[:2]


class BrandMark(QWidget):
    """A rounded, solid-filled square with the brand initials."""

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

        path = QPainterPath()
        path.addRoundedRect(rect, rect.height() / 3, rect.height() / 3)
        painter.fillPath(path, QColor(ACTIVE_PALETTE.accent))

        painter.setPen(QPen(QColor(ACTIVE_PALETTE.accent_hover), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        font = ui_font()
        font.setPixelSize(max(8, int(self._size * 0.38)))
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(ACTIVE_PALETTE.on_accent))
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
