"""Window activation: any "open the app" gesture must reach the window.

Regression cover for the reported defect — *with the app in the tray, only
the right-click menu opened it*. A tray click (single or double) and the
tray menu both have to surface the window, whether the user is signed out or
signed in, and whether the window was hidden or minimized
(docs/UI_SPEC.md §Window behavior, §System tray).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from app.config.settings import LocalConfig, ServerPolicy
from app.core.container import bootstrap_container
from app.ui.controller import UiController
from PySide6.QtWidgets import QSystemTrayIcon

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from app.core.container import Container
    from pytestqt.qtbot import QtBot

EMAIL = "employee@example.com"
PASSWORD = "s3cret!"


@pytest.fixture
def controller(qapp: object, tmp_path: Path) -> Iterator[UiController]:
    """A fully wired controller on a temp data dir, showing the login screen."""
    local = LocalConfig(
        data_dir=tmp_path / "data",
        log_level="DEBUG",
        credential_backend="none",
    )
    container: Container = bootstrap_container(
        local=local, server=ServerPolicy(), console_logging=False
    )
    container.open_database()
    ui = UiController(container, qapp)  # type: ignore[arg-type]
    ui.start()
    try:
        yield ui
    finally:
        ui.shutdown()
        container.close_database()


def _sign_in(ui: UiController, qtbot: QtBot) -> None:
    """Run the real login flow and wait for the dashboard to take over."""
    ui.login.submit.emit(EMAIL, PASSWORD)
    qtbot.waitUntil(lambda: ui.dashboard.isVisible(), timeout=5000)
    assert ui.dashboard.isVisible() is True


def _park_in_tray(ui: UiController) -> None:
    """Reproduce the reported state: no window, app only in the tray."""
    ui.login.hide()
    ui.dashboard.hide()


def test_start_shows_the_login_window(controller: UiController) -> None:
    assert controller.login.isVisible() is True
    assert controller.dashboard.isVisible() is False


def test_tray_click_opens_the_login_window(
    controller: UiController, qtbot: QtBot
) -> None:
    _park_in_tray(controller)

    with qtbot.waitSignal(controller.tray.open_dashboard, timeout=2000):
        controller.tray.handle_activated(QSystemTrayIcon.ActivationReason.Trigger)

    assert controller.login.isVisible() is True


def test_tray_menu_opens_the_window_too(controller: UiController) -> None:
    _park_in_tray(controller)

    controller.tray._open_action.trigger()

    assert controller.login.isVisible() is True


def test_tray_click_restores_a_minimized_dashboard(
    controller: UiController, qtbot: QtBot
) -> None:
    _sign_in(controller, qtbot)
    controller.dashboard.showMinimized()
    qtbot.wait(50)
    assert controller.dashboard.isMinimized() is True

    with qtbot.waitSignal(controller.tray.open_dashboard, timeout=2000):
        controller.tray.handle_activated(QSystemTrayIcon.ActivationReason.DoubleClick)

    assert controller.dashboard.isMinimized() is False
    assert controller.dashboard.isVisible() is True


def test_tray_click_reopens_the_hidden_dashboard(
    controller: UiController, qtbot: QtBot
) -> None:
    """Close-to-tray hides the window; a click must bring it back."""
    _sign_in(controller, qtbot)
    _park_in_tray(controller)

    with qtbot.waitSignal(controller.tray.open_dashboard, timeout=2000):
        controller.tray.handle_activated(QSystemTrayIcon.ActivationReason.Trigger)

    assert controller.dashboard.isVisible() is True
    assert controller.login.isVisible() is False


def test_show_main_window_reports_the_window_it_presented(
    controller: UiController, qtbot: QtBot
) -> None:
    """The entry point a second launch is wired to, and its return contract."""
    _park_in_tray(controller)

    assert controller.show_main_window() is controller.login
    assert controller.login.isVisible() is True

    _sign_in(controller, qtbot)
    _park_in_tray(controller)

    assert controller.show_main_window() is controller.dashboard
    assert controller.dashboard.isVisible() is True
