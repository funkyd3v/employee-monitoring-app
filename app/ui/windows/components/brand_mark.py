"""Artwork-first brand mark with an initials fallback."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from app.config.constants import APP_NAME_SHORT
from app.ui.theme import current_palette, ui_font
from app.ui.theme.assets import application_icon

if TYPE_CHECKING:
    from PySide6.QtGui import QPaintEvent

    from app.ui.theme.tokens import Palette

_INITIALS = APP_NAME_SHORT[:2]


class BrandMark(QWidget):
    """The application artwork, with a generated initials fallback."""

    def __init__(
        self,
        size: int = 24,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("BrandMark")
        self._size = size
        self._palette = current_palette()
        self._icon = application_icon()
        self.setFixedSize(size, size)

    def set_palette(self, palette: Palette) -> None:
        self._palette = palette
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(0, 0, -1, -1)

        if not self._icon.isNull():
            artwork = self._icon.pixmap(QSize(self._size, self._size))
            if not artwork.isNull():
                painter.drawPixmap(rect, artwork)
                painter.end()
                return

        path = QPainterPath()
        path.addRoundedRect(rect, rect.height() / 3, rect.height() / 3)
        painter.fillPath(path, QColor(self._palette.accent))
        painter.setPen(QPen(QColor(self._palette.accent_hover), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        font = ui_font()
        font.setPixelSize(max(8, int(self._size * 0.38)))
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(self._palette.on_accent))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, _INITIALS)
        painter.end()

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        return QSize(self._size, self._size)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt override)
        return QSize(self._size, self._size)
