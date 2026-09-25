"""Custom frameless title bar (docs/UI_SPEC.md §Window behavior).

Serves double duty as the dashboard's top bar: brand + app title on the
left, then an optional trailing cluster (Team Name + profile menu injected by
the dashboard) before the minimize/close buttons. There is deliberately no
maximize/expand affordance anywhere in the UI (product rule #8). Close is
*requested* via signal so the owning window decides the behavior
(minimize-to-tray while a session runs, never terminate).
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QMouseEvent  # noqa: TC002 — needed at runtime
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from app.config.constants import APP_NAME
from app.ui.theme.tokens import (
    BRAND_MARK_SIZE_SMALL,
    SPACING_MD,
    SPACING_SM,
    SPACING_XS,
    TITLE_BAR_HEIGHT,
    WINDOW_BUTTON_HEIGHT,
    WINDOW_BUTTON_WIDTH,
)
from app.ui.windows.components.brand_mark import BrandMark
from app.ui.windows.components.button import Button, ButtonRole


class TitleBar(QWidget):
    """Frameless-drag bar with brand mark, app name, and window buttons."""

    minimize_clicked = Signal()
    close_clicked = Signal()

    def __init__(self, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TitleBar")
        self.setFixedHeight(TITLE_BAR_HEIGHT)
        self._drag_pos: QPoint | None = None

        self._brand = BrandMark(BRAND_MARK_SIZE_SMALL)
        self._brand.setToolTip(APP_NAME)

        title = QLabel(APP_NAME)
        title.setObjectName("TitleBarTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self._min_button = Button(
            role=ButtonRole.GHOST,
            icon_name="minus",
            object_name="TitleMinButton",
        )
        self._min_button.setFixedSize(WINDOW_BUTTON_WIDTH, WINDOW_BUTTON_HEIGHT)
        self._min_button.setToolTip("Minimize")
        self._min_button.setAccessibleName("Minimize")
        self._min_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._min_button.clicked.connect(self.minimize_clicked.emit)

        self._close_button = Button(
            role=ButtonRole.GHOST,
            icon_name="x",
            object_name="TitleCloseButton",
        )
        self._close_button.setFixedSize(WINDOW_BUTTON_WIDTH, WINDOW_BUTTON_HEIGHT)
        self._close_button.setToolTip("Minimize to tray")
        self._close_button.setAccessibleName("Close")
        self._close_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._close_button.clicked.connect(self.close_clicked.emit)

        self._trailing = QHBoxLayout()
        self._trailing.setSpacing(SPACING_SM)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(SPACING_MD, SPACING_XS, SPACING_SM, SPACING_XS)
        layout.setSpacing(SPACING_SM)
        layout.addWidget(self._brand)
        layout.addWidget(title)
        layout.addStretch(1)
        layout.addLayout(self._trailing)
        layout.addWidget(self._min_button)
        layout.addWidget(self._close_button)

    def add_trailing(self, widget: QWidget) -> None:
        """Insert a right-aligned widget (team label, profile menu, …)."""
        self._trailing.addWidget(widget)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            window = self.window()
            handle = window.windowHandle()
            if handle is not None:
                try:
                    started = handle.startSystemMove()
                except RuntimeError:
                    started = False
                if started:
                    event.accept()
                    return
            self._drag_pos = (
                event.globalPosition().toPoint() - window.frameGeometry().topLeft()
            )
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.buttons() & Qt.MouseButton.LeftButton and self._drag_pos is not None:
            self.window().move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._drag_pos = None
        super().mouseReleaseEvent(event)
