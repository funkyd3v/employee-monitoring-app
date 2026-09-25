"""In-app confirmation modal (docs/UI_SPEC.md §Interaction & motion).

Logout and exit-while-working confirmations use this consistently styled
in-app modal, never a native OS message box. It is a
full-window scrim overlay with a centered card; confirm raises
:attr:`confirmed`, cancel/Escape raise :attr:`cancelled`, and the overlay
self-destructs as a :class:`Qt.WA_DeleteOnClose` child of ``parent``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, Signal
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from app.ui.theme import current_palette
from app.ui.theme.effects import ELEVATION_OVERLAY, MOTION
from app.ui.theme.tokens import (
    DIALOG_MIN_HEIGHT,
    DIALOG_WIDTH,
    SPACING_LG,
    SPACING_MD,
    SPACING_SM,
    SPACING_XL,
)
from app.ui.windows.components import Button, ButtonRole, Card

if TYPE_CHECKING:
    from PySide6.QtGui import QKeyEvent


class ConfirmDialog(QWidget):
    """A floating confirmation card over a dimmed scrim."""

    confirmed = Signal()
    cancelled = Signal()

    def __init__(
        self,
        parent: QWidget,
        *,
        title: str,
        message: str,
        confirm_text: str = "Confirm",
        cancel_text: str = "Cancel",
        danger: bool = False,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Widget)
        self.setObjectName("DialogScrim")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        title_label = QLabel(title)
        title_label.setObjectName("DialogTitle")
        message_label = QLabel(message)
        message_label.setObjectName("DialogMessage")
        message_label.setWordWrap(True)

        cancel = Button(cancel_text, role=ButtonRole.SECONDARY)
        confirm = Button(
            confirm_text,
            role=ButtonRole.DANGER if danger else ButtonRole.PRIMARY,
        )
        confirm.setDefault(True)

        cancel.clicked.connect(self.cancel)
        confirm.clicked.connect(self.confirm)

        buttons = QHBoxLayout()
        buttons.setSpacing(SPACING_SM)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(confirm)

        card_body = QVBoxLayout()
        card_body.setContentsMargins(SPACING_XL, SPACING_LG, SPACING_XL, SPACING_MD)
        card_body.setSpacing(SPACING_SM)
        card_body.addWidget(title_label)
        card_body.addWidget(message_label)
        card_body.addSpacing(SPACING_SM)
        card_body.addLayout(buttons)

        card = Card(object_name="DialogCard", parent=self)
        card.setLayout(card_body)

        card_shell = Card(object_name="DialogCardShell", parent=self)
        card_shell.setFixedWidth(DIALOG_WIDTH)
        card_shell.setMinimumHeight(DIALOG_MIN_HEIGHT)
        card_shell_layout = QVBoxLayout(card_shell)
        card_shell_layout.setContentsMargins(0, 0, 0, 0)
        card_shell_layout.addWidget(card)

        card_shell.apply_shadow(
            palette=current_palette(),
            elevation=ELEVATION_OVERLAY,
        )
        self._card_shell = card_shell
        self._card = card
        self._fade_effect = QGraphicsOpacityEffect(card)
        self._fade_effect.setOpacity(0.0)
        card.setGraphicsEffect(self._fade_effect)
        self._fade_animation: QPropertyAnimation | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addStretch(1)
        layout.addWidget(card_shell, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)

        confirm.setFocus(Qt.FocusReason.PopupFocusReason)

    def show_overlay(self) -> None:
        """Size to the parent and show (centers the card over the window)."""
        parent = self.parent()
        if isinstance(parent, QWidget):
            self.setGeometry(parent.rect())
        self.show()
        self.raise_()
        self.setFocus()

        self._fade_animation = QPropertyAnimation(self._fade_effect, b"opacity", self)
        self._fade_animation.setDuration(MOTION.dialog_duration_ms)
        self._fade_animation.setStartValue(0.0)
        self._fade_animation.setEndValue(1.0)
        self._fade_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade_animation.start()

    def confirm(self) -> None:
        self.confirmed.emit()
        self.close()

    def cancel(self) -> None:
        self.cancelled.emit()
        self.close()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.cancel()
            return
        super().keyPressEvent(event)
