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


class TestConnectionCaption:
    """The login screen must name the backend it is actually signing in to.

    It used to claim "offline-first" unconditionally, which is true of storage
    and false of the auth flow: a build that never found its ``.env`` runs the
    local dummy provider and refuses every real account while the screen still
    says no server is involved.
    """

    def test_api_mode_names_the_base_url(self) -> None:
        from types import SimpleNamespace

        from app.ui.controller import _connection_caption

        caption = _connection_caption(
            SimpleNamespace(mode="api", api_base_url="http://127.0.0.1:8000/api/v1")
        )

        assert "127.0.0.1:8000" in caption
        assert "offline" not in caption.lower()

    def test_api_mode_without_a_base_url_calls_it_out(self) -> None:
        from types import SimpleNamespace

        from app.ui.controller import _connection_caption

        caption = _connection_caption(SimpleNamespace(mode="api", api_base_url=""))

        assert "EM_API_BASE_URL" in caption

    def test_local_mode_keeps_the_offline_first_wording(self) -> None:
        from types import SimpleNamespace

        from app.ui.controller import _connection_caption

        caption = _connection_caption(
            SimpleNamespace(mode="local", api_base_url="http://127.0.0.1:8000")
        )

        assert "offline-first" in caption.lower()

    def test_controller_applies_the_caption_to_the_window(self, qtbot) -> None:
        from types import SimpleNamespace

        from app.ui.controller import _connection_caption

        window = make_window(qtbot)
        window.set_connection_status(
            _connection_caption(
                SimpleNamespace(mode="api", api_base_url="http://backend.test/api/v1")
            )
        )

        assert "backend.test" in window._connection_status.text()
        # The tooltip must not keep contradicting the caption.
        assert "offline-first" not in window._connection_status.toolTip().lower()


class TestLoginWorkerNeverGoesSilent:
    """A failure the auth stack did not classify must still reach the employee.

    Letting it escape stranded the spinner with no message and no log line,
    which is indistinguishable from a hang.
    """

    def test_unclassified_error_is_reported_and_logged(self, caplog) -> None:
        import logging

        from app.core.exceptions import AppError
        from app.ui.controller import _LoginWorker

        class BoomError(AppError):
            pass

        class ExplodingAuth:
            def login(self, email: str, password: str) -> object:
                raise BoomError("kaboom")

        worker = _LoginWorker(ExplodingAuth(), "a@b.com", "pw")  # type: ignore[arg-type]
        failures: list[object] = []
        worker.failed.connect(failures.append)

        with caplog.at_level(logging.WARNING, logger="employee_monitoring_agent"):
            worker.run()

        assert len(failures) == 1
        assert isinstance(failures[0], BoomError)
        assert any("unclassified" in r.getMessage() for r in caplog.records)

    def test_typed_auth_error_is_reported(self) -> None:
        from app.core.exceptions import InvalidCredentialsError
        from app.ui.controller import _LoginWorker

        class RefusingAuth:
            def login(self, email: str, password: str) -> object:
                raise InvalidCredentialsError("nope")

        worker = _LoginWorker(RefusingAuth(), "a@b.com", "pw")  # type: ignore[arg-type]
        failures: list[object] = []
        worker.failed.connect(failures.append)

        worker.run()

        assert len(failures) == 1


class TestFriendlyAuthMessage:
    """The message must name the actual cause.

    One message for every failure is how a misconfigured ``api_base_url`` looks
    like a typo in the employee's password: the app says "check your email and
    password" while the real answer is "the app is not talking to a server".
    """

    def test_wrong_credentials_blame_the_credentials(self) -> None:
        from app.core.exceptions import InvalidCredentialsError
        from app.ui.controller import _friendly_auth_message

        message = _friendly_auth_message(
            InvalidCredentialsError("invalid email or password")
        )

        assert message == (
            "We couldn't sign you in. Check your email and password and try again."
        )

    def test_an_unreachable_server_says_so(self) -> None:
        from app.core.exceptions import MonitoringServerUnavailableError
        from app.ui.controller import _friendly_auth_message

        message = _friendly_auth_message(MonitoringServerUnavailableError("boom"))

        assert "couldn't reach the monitoring server" in message
        assert "password" not in message

    def test_an_untyped_auth_failure_blames_neither(self) -> None:
        from app.core.exceptions import AuthenticationError
        from app.ui.controller import _friendly_auth_message

        message = _friendly_auth_message(AuthenticationError("internal detail"))

        assert message == "We couldn't sign you in. Please try again."
        assert "internal detail" not in message

    def test_a_non_auth_failure_stays_generic(self) -> None:
        from app.ui.controller import _friendly_auth_message

        assert _friendly_auth_message(ValueError("trace me")) == (
            "Something went wrong while signing in. Please try again."
        )
