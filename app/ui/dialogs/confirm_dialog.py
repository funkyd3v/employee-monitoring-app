"""In-app confirmation modal (docs/UI_SPEC.md §Interaction & motion).

All confirmations — Check Out, Logout, Exit-while-working — use this
consistently styled in-app modal, never a native OS message box. It is a
full-window scrim overlay with a centered card; confirm raises
:attr:`confirmed`, cancel/Escape raise :attr:`cancelled`, and the overlay
self-destructs as a :class:`Qt.WA_DeleteOnClose` child of ``parent``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

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

        cancel = QPushButton(cancel_text)
        cancel.setProperty("role", "secondary")
        confirm = QPushButton(confirm_text)
        confirm.setProperty("role", "danger" if danger else "primary")
        confirm.setDefault(True)

        cancel.clicked.connect(self.cancel)
        confirm.clicked.connect(self.confirm)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(confirm)

        card_body = QVBoxLayout()
        card_body.setContentsMargins(28, 24, 28, 20)
        card_body.setSpacing(10)
        card_body.addWidget(title_label)
        card_body.addWidget(message_label)
        card_body.addSpacing(6)
        card_body.addLayout(buttons)

        card = QWidget(self)
        card.setObjectName("DialogCard")
        card.setMaximumWidth(440)
        card.setLayout(card_body)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addStretch(1)
        layout.addWidget(card, 0, Qt.AlignmentFlag.AlignHCenter)
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
