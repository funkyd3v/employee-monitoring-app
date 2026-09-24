"""Widget tests for the login window (docs/UI_SPEC.md §Login screen).

The window is pure presentation: it validates non-empty fields and emits
``submit``; loading/error slots are driven by the controller. Tests verify the
seam without touching auth.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.ui.windows.components.spinner import Spinner
from app.ui.windows.login_window import LoginWindow
from PySide6.QtCore import Qt

if TYPE_CHECKING:
    from pytestqt.qtbot import QtBot


def make_window(qtbot: QtBot) -> LoginWindow:
    window = LoginWindow()
    window.resize(900, 600)
    qtbot.addWidget(window)
    window.show()
    return window


def test_submit_emits_credentials(qtbot: QtBot) -> None:
    window = make_window(qtbot)
    window.email_field.edit.setText("ada@company.com")
    window.password_field.edit.setText("hunter22")

    with qtbot.waitSignal(window.submit, timeout=300) as blocker:
        qtbot.mouseClick(window.sign_in_button, Qt.MouseButton.LeftButton)

    assert blocker.args == ["ada@company.com", "hunter22"]


def test_empty_fields_block_submit_and_show_errors(qtbot: QtBot) -> None:
    window = make_window(qtbot)

    emitted: list[tuple[str, str]] = []
    window.submit.connect(lambda email, pw: emitted.append((email, pw)))

    with qtbot.assertNotEmitted(window.submit, wait=200):
        qtbot.mouseClick(window.sign_in_button, Qt.MouseButton.LeftButton)

    assert emitted == []
    assert window.email_field.error_label.isVisible()
    assert window.password_field.error_label.isVisible()


def test_missing_email_only_marks_email(qtbot: QtBot) -> None:
    window = make_window(qtbot)
    window.password_field.edit.setText("secret")

    qtbot.mouseClick(window.sign_in_button, Qt.MouseButton.LeftButton)

    assert window.email_field.error_label.isVisible()
    assert not window.password_field.error_label.isVisible()


def test_loading_disables_input_shows_spinner(qtbot: QtBot) -> None:
    window = make_window(qtbot)

    window.set_loading(True)

    assert not window.sign_in_button.isEnabled()
    assert not window.email_field.edit.isEnabled()
    assert not window.password_field.edit.isEnabled()
    spinner = window.findChild(Spinner)
    assert spinner is not None
    assert spinner.isVisible()

    window.set_loading(False)
    assert window.sign_in_button.isEnabled()


def test_show_auth_error_renders_friendly_message(qtbot: QtBot) -> None:
    window = make_window(qtbot)

    window.show_auth_error("We couldn't sign you in. Please check your credentials.")

    assert "credentials" in window._submit_error.text()
    assert window._submit_error.isVisible()


def test_clear_error_hides_submit_error(qtbot: QtBot) -> None:
    window = make_window(qtbot)
    window.show_auth_error("Something went wrong.")
    assert window._submit_error.isVisible()

    window.clear_error()
    assert not window._submit_error.isVisible()
