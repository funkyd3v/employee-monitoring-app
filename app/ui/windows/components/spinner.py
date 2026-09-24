"""Loading spinner for in-flight operations (login, long-running actions).

Painted with ``QPainter`` on a timer — no image assets, theme-driven color,
and it clears automatically when stopped. Keyboard/AT focus never touches it
(it is purely decorative feedback next to a disabled button).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

from app.ui.theme.tokens import ACTIVE_PALETTE

if TYPE_CHECKING:
    from PySide6.QtGui import QPaintEvent


class Spinner(QWidget):
    """An infinitely rotating arc; call :meth:`start`/:meth:`stop`."""

    def __init__(
        self,
        *,
        size: int = 20,
        color: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._color = QColor(color) if color else QColor(ACTIVE_PALETTE.accent)
        self._angle = 0
        self.setFixedSize(size, size)
        self.setHidden(True)

        self._timer = QTimer(self)
        self._timer.setInterval(16)  # ~60fps rotation
        self._timer.timeout.connect(self._rotate)

    def _rotate(self) -> None:
        self._angle = (self._angle + 9) % 360
        self.update()

    def start(self) -> None:
        self.setVisible(True)
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self.setVisible(False)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(self._color, max(2, self.width() // 8))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        margin = pen.width()
        rect = self.rect().adjusted(margin, margin, -margin, -margin)
        painter.drawArc(rect, -self._angle * 16, 280 * 16)
        painter.end()
