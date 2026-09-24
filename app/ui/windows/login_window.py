"""Login screen (docs/UI_SPEC.md §Login screen).

Pure presentation: it validates that fields are non-empty, emits
:attr:`submit` with credentials, and exposes loading/error slots the
controller drives. It does *not* call the auth provider — that happens on a
worker thread in the controller, which keeps the Qt thread unblocked and the
window independently testable. Errors shown to the user are always
friendly prose, never raw exception text.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.ui.windows.base_window import FramelessWindow
from app.ui.windows.components import BrandMark, FormField, Spinner

_CONNECTION_LOCAL = "Offline-first \u00b7 data stays on this device"


class LoginWindow(FramelessWindow):
    """Frameless login card with floating labels, toggle, validation."""

    submit = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("LoginWindow")

        body = self.body()
        root = QVBoxLayout(body)
        root.setContentsMargins(0, 0, 0, 0)

        center = QWidget(body)
        center.setObjectName("LoginCenter")

        card = QWidget(center)
        card.setObjectName("LoginCard")
        card.setMaximumWidth(430)

        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(48)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 130))
        card.setGraphicsEffect(shadow)

        brand = BrandMark(56)
        brand.setFixedSize(56, 56)

        title = QLabel("Welcome back")
        title.setObjectName("PanelTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        subtitle = QLabel("Sign in to continue to your workspace")
        subtitle.setObjectName("PanelSubtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._email = FormField(label="Email", placeholder="you@company.com")
        self._password = FormField(
            label="Password", placeholder="Enter your password", is_password=True
        )
        self._email.return_pressed.connect(self._on_submit_clicked)
        self._password.return_pressed.connect(self._on_submit_clicked)

        self._spinner = Spinner(size=18)
        self._submit_button = QPushButton("Sign In")
        self._submit_button.setObjectName("PrimaryCta")
        self._submit_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._submit_button.clicked.connect(self._on_submit_clicked)

        button_row = QHBoxLayout()
        button_row.setSpacing(10)
        button_row.addWidget(self._spinner, 0, Qt.AlignmentFlag.AlignVCenter)
        button_row.addWidget(self._submit_button, 1)

        self._submit_error = QLabel("")
        self._submit_error.setObjectName("FieldError")
        self._submit_error.setWordWrap(True)
        self._submit_error.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._submit_error.hide()

        self._connection_status = QLabel(_CONNECTION_LOCAL)
        self._connection_status.setObjectName("PanelCaption")
        self._connection_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._connection_status.setToolTip(
            "The agent works fully offline-first; data is kept on this device."
        )

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(36, 34, 36, 28)
        card_layout.setSpacing(10)
        card_layout.addWidget(brand, 0, Qt.AlignmentFlag.AlignHCenter)
        card_layout.addSpacing(8)
        card_layout.addWidget(title)
        card_layout.addWidget(subtitle)
        card_layout.addSpacing(18)
        card_layout.addWidget(self._email)
        card_layout.addWidget(self._password)
        card_layout.addSpacing(8)
        card_layout.addLayout(button_row)
        card_layout.addWidget(self._submit_error)
        card_layout.addSpacing(10)
        card_layout.addWidget(self._connection_status)

        root.addStretch(1)
        root.addWidget(center, 0, Qt.AlignmentFlag.AlignHCenter)
        root.addStretch(1)

        card_outer = QVBoxLayout(center)
        card_outer.setContentsMargins(24, 24, 24, 24)
        card_outer.addWidget(card)

    # ── Presentation API (controller-facing) ───────────────────────────────
    def set_loading(self, loading: bool) -> None:
        """Disable input + show the spinner during in-flight auth."""
        if loading:
            self._spinner.start()
        else:
            self._spinner.stop()
        self._email.edit.setEnabled(not loading)
        self._password.edit.setEnabled(not loading)
        self._submit_button.setEnabled(not loading)

    def show_auth_error(self, message: str) -> None:
        """Non-technical error under the CTA (validation/credential failure)."""
        self._email.clear_error()
        self._password.clear_error()
        self._submit_error.setText(message)
        self._submit_error.show()
        self._password.shake()

    def clear_error(self) -> None:
        self._submit_error.hide()
        self._email.clear_error()
        self._password.clear_error()

    def set_connection_status(self, text: str) -> None:
        self._connection_status.setText(text)

    def reset_form(self) -> None:
        self._email.clear()
        self._password.clear()
        self.clear_error()
        self.set_loading(False)
        self._email.focus()

    @property
    def email_field(self) -> FormField:
        return self._email

    @property
    def password_field(self) -> FormField:
        return self._password

    @property
    def sign_in_button(self) -> QPushButton:
        return self._submit_button

    # ── Internal ───────────────────────────────────────────────────────────
    def _on_submit_clicked(self) -> None:
        self.clear_error()
        email = self._email.text().strip()
        password = self._password.text()

        valid = True
        if not email:
            self._email.set_error("Enter your email to continue.")
            valid = False
        if not password:
            self._password.set_error("Enter your password to continue.")
            valid = False
        if not valid:
            return
        self.submit.emit(email, password)
