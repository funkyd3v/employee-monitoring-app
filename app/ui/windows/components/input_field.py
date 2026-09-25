"""Form input with label caption, password toggle, inline error, and shake.

Implements the login-screen input behavior from docs/UI_SPEC.md §Login
screen: a caption 'floating label' above the field, a password-visibility
toggle (text-based, keyboard accessible — no emoji/icon-only control),
inline validation (red border + message) and a subtle shake on invalid
submit. The container exposes a clean `text()`/`set_error()` surface so
callers never reach into the QLineEdit.
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.ui.theme.effects import MOTION
from app.ui.theme.tokens import SPACING_XS


class FormField(QWidget):
    """Labeled input with optional password toggle, error, and shake."""

    text_changed = Signal(str)
    return_pressed = Signal()

    def __init__(
        self,
        *,
        label: str,
        placeholder: str,
        is_password: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._field_container = QWidget(self)
        self._field_container.setObjectName("FieldContainer")

        self._label = QLabel(label)
        self._label.setObjectName("FieldLabel")

        self._edit = QLineEdit()
        self._edit.setObjectName("InputField")
        self._edit.setAccessibleName(label)
        self._edit.setPlaceholderText(placeholder)
        self._edit.setTextMargins(0, 0, 0, 0)
        self._edit.textChanged.connect(self.text_changed.emit)
        self._edit.returnPressed.connect(self.return_pressed.emit)

        self._error = QLabel("")
        self._error.setObjectName("FieldError")
        self._error.setWordWrap(True)
        self._error.hide()

        row = QHBoxLayout(self._field_container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        if is_password:
            self._edit.setEchoMode(QLineEdit.EchoMode.Password)
            self._toggle = QToolButton()
            self._toggle.setObjectName("PasswordToggle")
            self._toggle.setText("Show")
            self._toggle.setToolTip("Show password")
            self._toggle.setCheckable(True)
            self._toggle.setCursor(Qt.CursorShape.PointingHandCursor)
            self._toggle.toggled.connect(self._on_toggle)
            row.addWidget(self._edit, 1)
            row.addWidget(self._toggle)
        else:
            row.addWidget(self._edit, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING_XS)
        layout.addWidget(self._label)
        layout.addWidget(self._field_container)
        layout.addWidget(self._error)

    @property
    def edit(self) -> QLineEdit:
        """The underlying line edit (tests / advanced styling)."""
        return self._edit

    @property
    def error_label(self) -> QLabel:
        """Inline error label (tests / accessibility)."""
        return self._error

    def text(self) -> str:
        return self._edit.text()

    def set_text(self, value: str) -> None:
        self._edit.setText(value)

    def clear(self) -> None:
        self._edit.clear()
        self.clear_error()

    def focus(self) -> None:
        self._edit.setFocus(Qt.FocusReason.OtherFocusReason)

    def is_password(self) -> bool:
        return self._edit.echoMode() == QLineEdit.EchoMode.Password

    def set_error(self, message: str) -> None:
        """Show an inline validation/auth error and flag the field."""
        self._error.setText(message)
        self._error.show()
        self._edit.setProperty("error", True)
        self._edit.style().unpolish(self._edit)
        self._edit.style().polish(self._edit)
        self.shake()

    def clear_error(self) -> None:
        self._error.clear()
        self._error.hide()
        self._edit.setProperty("error", False)
        self._edit.style().unpolish(self._edit)
        self._edit.style().polish(self._edit)

    def shake(self) -> None:
        """Subtle horizontal shake on invalid submit (UI_SPEC §Login)."""
        origin = self._edit.pos()
        waypoints = [
            (0.00, origin),
            (0.20, origin + QPoint(9, 0)),
            (0.42, origin + QPoint(-8, 0)),
            (0.62, origin + QPoint(6, 0)),
            (0.80, origin + QPoint(-4, 0)),
            (1.00, origin),
        ]
        animation = QPropertyAnimation(self._edit, b"pos", self)
        animation.setDuration(MOTION.shake_duration_ms)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        for fraction, point in waypoints:
            animation.setKeyValueAt(fraction, point)
        animation.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

    def _on_toggle(self, reveal: bool) -> None:
        self._edit.setEchoMode(
            QLineEdit.EchoMode.Normal if reveal else QLineEdit.EchoMode.Password
        )
        self._toggle.setText("Hide" if reveal else "Show")
        self._toggle.setToolTip("Hide password" if reveal else "Show password")
        if reveal:
            self._edit.setCursorPosition(len(self._edit.text()))
