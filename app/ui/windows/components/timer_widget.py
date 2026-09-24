"""Display-only elapsed-time label.

Timer correctness (docs/ENGINEERING_RULES.md §Timer Correctness): this widget
is a pure projection. It never accumulates or owns time — the caller
recomputes the value each tick (``set_elapsed``) from persisted timestamps
and the widget only formats it. Monospace face keeps digits from jittering.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QWidget

from app.config.constants import APP_NAME
from app.ui.theme import mono_font
from app.ui.theme.tokens import ACTIVE_TIMER_FONT_PT


def format_elapsed_hms(total_seconds: int) -> str:
    """Format ``total_seconds`` as ``HH:MM:SS`` (hours unbounded)."""
    seconds = max(0, int(total_seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


class TimerWidget(QLabel):
    """Monospaced, non-interactive elapsed-time display."""

    def __init__(self, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TimerLabel")
        self._font_size = ACTIVE_TIMER_FONT_PT
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(84)
        self._update_font()
        self.set_elapsed(0)
        self.setToolTip(f"{APP_NAME} elapsed time (recomputed, never counted)")

    def set_elapsed(self, total_seconds: int) -> None:
        """Render a fresh projection of *current* elapsed seconds."""
        self.setText(format_elapsed_hms(total_seconds))

    def set_font_size(self, points: int) -> None:
        self._font_size = points
        self._update_font()

    def _update_font(self) -> None:
        font = mono_font()
        font.setPointSize(self._font_size)
        font.setWeight(font.Weight.DemiBold)
        self.setFont(font)
