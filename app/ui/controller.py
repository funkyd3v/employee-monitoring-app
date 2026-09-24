"""UI controller (docs/UI_SPEC.md §Interaction & motion).

Owns the two windows, the tray, and the wiring between them and the
container's services — this is the only place UI presentation maps onto
business actions. Auth *login* runs on a worker ``QThread`` (never the Qt
thread); every result crosses back through a Qt signal, keeping the UI
thread unblocked (docs/ENGINEERING_RULES.md §Threading Discipline).

The dashboard receives the :class:`SessionService` as its presenter
port; identical actions stay consistent whether they come from the window
buttons or the tray menu.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from app.core.exceptions import AuthenticationError
from app.core.logging import get_logger
from app.domain.auth.auth import AuthenticatedUser
from app.domain.sessions.state_machine import AppState
from app.ui.dialogs.confirm_dialog import ConfirmDialog
from app.ui.tray.tray_manager import TrayManager
from app.ui.view_models import DashboardUser
from app.ui.windows.dashboard_window import DashboardWindow
from app.ui.windows.login_window import LoginWindow

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication

    from app.core.container import Container
    from app.services.auth_service import AuthService
    from app.services.session_service import SessionService

_ACTIVE_STATES: frozenset[AppState] = frozenset({AppState.WORKING, AppState.BREAK})


class _LoginWorker(QObject):
    """Runs a single auth login off the Qt thread; emits its outcome."""

    succeeded = Signal(object)
    failed = Signal(object)

    def __init__(
        self,
        auth: AuthService,
        email: str,
        password: str,
        *,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._auth = auth
        self._email = email
        self._password = password

    @Slot()
    def run(self) -> None:
        try:
            user = self._auth.login(email=self._email, password=self._password)
        except AuthenticationError as exc:
            self.failed.emit(exc)
            return
        self.succeeded.emit(user)


class UiController(QObject):
    """Wires windows + tray to the container's services and the Qt loop."""

    def __init__(
        self,
        container: Container,
        app: QApplication,
        *,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._container = container
        self._auth: AuthService = container.auth_service
        self._sessions: SessionService = container.session_service
        self._app = app
        self._logger = get_logger("ui.controller")
        self._thread: QThread | None = None
        self._worker: _LoginWorker | None = None

        self.login = LoginWindow()
        self.dashboard = DashboardWindow(self._sessions)
        self.tray = TrayManager()

        self._wire()

    # ── Wiring ─────────────────────────────────────────────────────────────
    def _wire(self) -> None:
        self.login.submit.connect(self._start_login)
        self.dashboard.logout_requested.connect(self._logout)
        self.login.closed_to_tray.connect(self._hide_login_only)

        self.tray.open_dashboard.connect(self._show_dashboard)
        self.tray.take_break.connect(self._tray_take_break)
        self.tray.check_out.connect(self._tray_check_out)
        self.tray.logout.connect(self._tray_logout)
        self.tray.exit_requested.connect(self._exit)

    def start(self) -> None:
        """Boot the UI: a restored session shows the dashboard, else login."""
        self.tray.show()
        user = self._auth.current_user()
        if user is not None:
            self._enter_dashboard(user)
        else:
            self._enter_login()

    def shutdown(self) -> None:
        """Park worker threads and hide windows (no auth teardown here)."""
        if self._thread is not None and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(2000)
        self.login.hide()
        self.dashboard.hide()
        self.tray.hide()

    # ── Screen transitions ─────────────────────────────────────────────────
    def _enter_login(self) -> None:
        self.dashboard.hide()
        self.login.reset_form()
        self.login.show()
        self.login.raise_()

    def _enter_dashboard(self, user: AuthenticatedUser) -> None:
        self.login.hide()
        self._sessions.set_user(self._auth.current_user_id())
        self.dashboard.set_user(
            DashboardUser(
                display_name=user.display_name or user.email,
                email=user.email,
                team_name=user.team_name,
            )
        )
        view = self._sessions.tick()
        self.dashboard.set_view(view)
        self.tray.set_view(view)
        self.dashboard.show()
        self.dashboard.raise_()

    def _show_dashboard(self) -> None:
        if not self._auth.is_authenticated():
            return
        if not self.dashboard.isVisible():
            self.dashboard.show()
        self.dashboard.raise_()
        self.dashboard.activateWindow()
        view = self._sessions.tick()
        self.dashboard.set_view(view)
        self.tray.set_view(view)

    def _hide_login_only(self) -> None:
        # Login close-to-tray keeps the app alive (Exit lives in the tray).
        self.login.hide()

    # ── Login flow (worker thread) ─────────────────────────────────────────
    def _start_login(self, email: str, password: str) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        self.login.set_loading(True)

        thread = QThread(self)
        worker = _LoginWorker(self._auth, email, password)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.succeeded.connect(self._on_login_succeeded)
        worker.failed.connect(self._on_login_failed)
        worker.destroyed.connect(self._release_worker)
        thread.finished.connect(thread.deleteLater)

        self._thread = thread
        self._worker = worker
        thread.start()

    @Slot(object)
    def _on_login_succeeded(self, user: object) -> None:
        self.login.set_loading(False)
        if not isinstance(user, AuthenticatedUser):
            self._logger.error("login worker returned an unexpected payload")
            self.login.show_auth_error(
                "Something went wrong while signing in. Please try again."
            )
            return
        self._logger.info("login succeeded — entering dashboard")
        self._enter_dashboard(user)

    @Slot(object)
    def _on_login_failed(self, exc: object) -> None:
        self.login.set_loading(False)
        self.login.show_auth_error(_friendly_auth_message(exc))

    def _release_worker(self) -> None:
        self._worker = None
        self._thread = None

    # ── Session actions from the tray ──────────────────────────────────────
    def _tray_take_break(self) -> None:
        view = self._sessions.take_break()
        self.dashboard.set_view(view)
        self.tray.set_view(view)

    def _tray_check_out(self) -> None:
        self.dashboard.request_check_out()

    def _tray_logout(self) -> None:
        self.dashboard.request_logout()

    def _logout(self) -> None:
        view = self._sessions.tick()
        if view.state in _ACTIVE_STATES:
            self._sessions.check_out()
        self._auth.logout()
        try:
            self._enter_login()
        except Exception:
            self._logger.exception("logout teardown failed")
        self.tray.set_view(self._sessions.tick())

    def _exit(self) -> None:
        """Exit = quit, but confirm when an unfinished session would be lost."""
        session = self._sessions.machine.session
        if session is not None and session.ended_at is None:
            dialog = ConfirmDialog(
                self.dashboard,
                title="Exit the agent?",
                message="You have an active work session. Exiting will leave it "
                "unchecked-out — it won't be counted.",
                confirm_text="Exit",
                danger=True,
            )
            dialog.confirmed.connect(self._quit)
            dialog.show_overlay()
        else:
            self._quit()

    def _quit(self) -> None:
        # Let the app's own quit bookkeeping run on the next loop pass.
        QTimer.singleShot(0, self._app.quit)


def _friendly_auth_message(exc: object) -> str:
    """Friendly prose; never raw exception text (UI_SPEC error rules)."""
    if isinstance(exc, AuthenticationError):
        return "We couldn't sign you in. Check your email and password and try again."
    return "Something went wrong while signing in. Please try again."


__all__ = ["UiController"]
