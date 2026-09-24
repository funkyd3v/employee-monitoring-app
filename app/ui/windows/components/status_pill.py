"""Status pill (docs/UI_SPEC.md §Main dashboard).

A slim pill reflecting real-time session state: a colored dot + label with a
soft, non-distracting pulse on the ACTIVE state only. Purely presentational —
the caller feeds an explicit :class:`PillState`; it never infers state.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QGraphicsOpacityEffect, QHBoxLayout, QLabel, QWidget

from app.ui.theme.tokens import ACTIVE_PALETTE

if TYPE_CHECKING:
    from PySide6.QtGui import QPaintEvent

_TEXTS: dict[str, str] = {
    "ACTIVE": "Active",
    "IDLE": "Idle",
    "BREAK": "On Break",
    "OFF": "Checked Out",
}


class PillState(StrEnum):
    """Display states the pill can reflect."""

    ACTIVE = "ACTIVE"
    IDLE = "IDLE"
    BREAK = "BREAK"
    OFF = "OFF"


class _Dot(QWidget):
    """Small solid-color circle for the pill."""

    def __init__(self, color: QColor, radius: int = 5) -> None:
        super().__init__()
        self._color = color
        self._radius = radius
        self.setFixedSize(radius * 2, radius * 2)

    def set_color(self, color: QColor) -> None:
        self._color = color
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._color)
        center = self.rect().center()
        painter.drawEllipse(center, self._radius, self._radius)
        painter.end()


def _state_color(state: PillState) -> QColor:
    if state is PillState.ACTIVE:
        return QColor(ACTIVE_PALETTE.success)
    if state is PillState.IDLE:
        return QColor(ACTIVE_PALETTE.warning)
    return QColor(ACTIVE_PALETTE.text_secondary)


class StatusPill(QWidget):
    """Colored-dot status indicator with soft pulse on ACTIVE."""

    def __init__(self, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("StatusPill")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._state = PillState.OFF

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity)
        self.setProperty("pillState", self._state.value)

        self._pulse = QPropertyAnimation(self._opacity, b"opacity", self)
        self._pulse.setDuration(1800)
        self._pulse.setStartValue(1.0)
        self._pulse.setKeyValueAt(0.5, 0.78)
        self._pulse.setEndValue(1.0)
        self._pulse.setLoopCount(-1)
        self._pulse.setEasingCurve(QEasingCurve.Type.InOutSine)

        self._dot = _Dot(_state_color(self._state))
        self._label = QLabel(_TEXTS[self._state.value])
        self._label.setObjectName("PillText")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 5, 12, 5)
        layout.setSpacing(8)
        layout.addWidget(self._dot)
        layout.addWidget(self._label)

    @property
    def state(self) -> PillState:
        return self._state

    def set_state(self, state: PillState) -> None:
        """Switch the pill; starts the soft pulse only for ACTIVE."""
        if state is self._state:
            return
        self._state = state
        self.setProperty("pillState", state.value)
        self._dot.set_color(_state_color(state))
        self._label.setText(_TEXTS[state.value])
        self.setToolTip(self._tooltip_for(state))
        self.style().unpolish(self)
        self.style().polish(self)
        if state is PillState.ACTIVE:
            self._pulse.start()
        else:
            self._pulse.stop()
            self._opacity.setOpacity(1.0)

    @staticmethod
    def _tooltip_for(state: PillState) -> str:
        return {
            PillState.ACTIVE: "You are active — monitoring is running.",
            PillState.IDLE: "Idle detected — input has not been seen for a while.",
            PillState.BREAK: "On break — monitoring is paused.",
            PillState.OFF: "Currently checked out.",
        }[state]
