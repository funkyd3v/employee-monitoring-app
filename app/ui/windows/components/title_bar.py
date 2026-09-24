"""Custom frameless title bar (docs/UI_SPEC.md §Window behavior).

Serves double duty as the dashboard's top bar: brand + app title on the
left, then an optional trailing cluster (Team Name + avatar menu injected by
the dashboard) before the minimize/close buttons. There is deliberately no
maximize/expand affordance anywhere in the UI (product rule #8). Close is
*requested* via signal so the owning window decides the behavior
(minimize-to-tray while a session runs, never terminate).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from app.config.constants import APP_NAME
from app.ui.windows.components.brand_mark import BrandMark

if TYPE_CHECKING:
    from PySide6.QtGui import QMouseEvent


class TitleBar(QWidget):
    """Frameless-drag bar with brand mark, app name, and window buttons."""

    minimize_clicked = Signal()
    close_clicked = Signal()

    def __init__(self, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TitleBar")
        self.setFixedHeight(46)

        self._brand = BrandMark(22)
        self._brand.setToolTip(APP_NAME)

        title = QLabel(APP_NAME)
        title.setObjectName("TitleBarTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self._min_button = QPushButton("\u2212")  # MINUS SIGN
        self._min_button.setObjectName("TitleMinButton")
        self._min_button.setFixedSize(42, 34)
        self._min_button.setToolTip("Minimize")
        self._min_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._min_button.clicked.connect(self.minimize_clicked.emit)

        self._close_button = QPushButton("\u2715")  # MULTIPLICATION X
        self._close_button.setObjectName("TitleCloseButton")
        self._close_button.setFixedSize(42, 34)
        self._close_button.setToolTip("Minimize to tray")
        self._close_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._close_button.clicked.connect(self.close_clicked.emit)

        self._trailing = QHBoxLayout()
        self._trailing.setSpacing(10)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 6, 8, 6)
        layout.setSpacing(8)
        layout.addWidget(self._brand)
        layout.addWidget(title)
        layout.addStretch(1)
        layout.addLayout(self._trailing)
        layout.addWidget(self._min_button)
        layout.addWidget(self._close_button)

    def add_trailing(self, widget: QWidget) -> None:
        """Insert a right-aligned widget (team label, avatar menu, …)."""
        self._trailing.addWidget(widget)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            window = self.window()
            if hasattr(window, "startSystemMove"):
                window.startSystemMove()
        super().mousePressEvent(event)
