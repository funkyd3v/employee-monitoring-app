"""Tests for the system tray (docs/UI_SPEC.md §System tray).

``tray_state_for`` maps projections to icon states and is pure; the
:class:`TrayManager` enables/disables actions from the same projection so the
tray can never offer an action the machine rejects.

Clicking the icon is also an "open the app" gesture — a single click and a
double click both surface the window, without the right-click menu.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from app.domain.activity.activity import ActivityState
from app.domain.sessions.state_machine import AppState
from app.services.session_service import SessionView
from app.ui.tray.tray_manager import (
    TrayManager,
    TrayState,
    _paint_tray_icon,
    tray_state_for,
)
from PySide6.QtWidgets import QSystemTrayIcon

if TYPE_CHECKING:
    from PySide6.QtGui import QImage
    from pytestqt.qtbot import QtBot

_AT = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)


def _view(state: AppState, **overrides: object) -> SessionView:
    defaults: dict[str, object] = {
        "state": state,
        "activity_state": ActivityState.ACTIVE,
    }
    defaults.update(overrides)
    return SessionView(**defaults)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (AppState.WORKING, TrayState.CHECKED_IN),
        (AppState.BREAK, TrayState.ON_BREAK),
        (AppState.READY, TrayState.CHECKED_OUT),
        (AppState.COMPLETED, TrayState.CHECKED_OUT),
        (AppState.LOGGED_OUT, TrayState.CHECKED_OUT),
    ],
)
def test_tray_state_mapping(state: AppState, expected: TrayState) -> None:
    assert tray_state_for(_view(state)) is expected


def test_working_is_checked_in_even_when_idle() -> None:
    view = _view(AppState.WORKING, activity_state=ActivityState.IDLE)
    assert tray_state_for(view) is TrayState.CHECKED_IN


def _contains_color(image: QImage) -> bool:
    for y in range(0, image.height(), 8):
        for x in range(0, image.width(), 8):
            red, green, blue, _ = image.pixelColor(x, y).getRgb()
            if max(red, green, blue) - min(red, green, blue) > 2:
                return True
    return False


def test_tray_icon_uses_canonical_app_artwork(qapp: object) -> None:
    image = _paint_tray_icon(TrayState.CHECKED_IN).pixmap(256, 256).toImage()

    assert not image.isNull()
    assert _contains_color(image)


@pytest.mark.parametrize("state", [TrayState.ON_BREAK, TrayState.CHECKED_OUT])
def test_inactive_tray_icons_are_gray(qapp: object, state: TrayState) -> None:
    image = _paint_tray_icon(state).pixmap(256, 256).toImage()

    assert not image.isNull()
    assert image.hasAlphaChannel()
    assert not _contains_color(image)


def test_tray_manager_action_enabled_from_projection(qapp: object) -> None:
    manager = TrayManager()

    manager.set_view(
        _view(
            AppState.WORKING,
            can_take_break=True,
            can_check_out=True,
        )
    )
    assert not manager._check_in_action.isEnabled()
    assert manager._break_action.isEnabled()
    assert not manager._resume_action.isEnabled()
    assert manager._checkout_action.isEnabled()
    assert manager._status_action.text() == "Current Status: Checked In"

    manager.set_view(_view(AppState.BREAK, can_resume=True, can_check_out=True))
    assert manager._resume_action.isEnabled()

    manager.set_view(_view(AppState.READY, can_check_in=True))
    assert manager._check_in_action.isEnabled()
    assert not manager._break_action.isEnabled()
    assert not manager._resume_action.isEnabled()
    assert not manager._checkout_action.isEnabled()
    assert manager._status_action.text() == "Current Status: Checked Out"

    actions = manager._menu.actions()
    assert actions.index(manager._check_in_action) + 1 == actions.index(
        manager._break_action
    )


def test_tray_manager_signals(qtbot: QtBot) -> None:
    manager = TrayManager()

    with qtbot.waitSignal(manager.open_dashboard, timeout=300):
        manager._open_action.trigger()

    manager.set_view(_view(AppState.READY, can_check_in=True))
    with qtbot.waitSignal(manager.check_in, timeout=300):
        manager._check_in_action.trigger()

    manager.set_view(_view(AppState.BREAK, can_resume=True))
    with qtbot.waitSignal(manager.resume, timeout=300):
        manager._resume_action.trigger()

    with qtbot.waitSignal(manager.exit_requested, timeout=300):
        manager._exit_action.trigger()


def test_clicking_the_tray_icon_opens_the_app(qtbot: QtBot) -> None:
    """The icon is wired to the app, not just to its own handler."""
    manager = TrayManager()

    with qtbot.waitSignal(manager.open_dashboard, timeout=2000):
        manager.tray_icon.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)


@pytest.mark.parametrize(
    "reason",
    [
        QSystemTrayIcon.ActivationReason.Trigger,
        QSystemTrayIcon.ActivationReason.DoubleClick,
    ],
)
def test_single_and_double_click_both_open_the_app(
    qtbot: QtBot, reason: QSystemTrayIcon.ActivationReason
) -> None:
    manager = TrayManager()

    with qtbot.waitSignal(manager.open_dashboard, timeout=2000):
        manager.handle_activated(reason)


def test_double_click_opens_the_app_only_once(qtbot: QtBot) -> None:
    """Windows reports a double click as Trigger *and* DoubleClick."""
    manager = TrayManager()

    with qtbot.waitSignal(manager.open_dashboard, timeout=2000):
        manager.handle_activated(QSystemTrayIcon.ActivationReason.Trigger)
        manager.handle_activated(QSystemTrayIcon.ActivationReason.DoubleClick)

    with qtbot.assertNotEmitted(manager.open_dashboard):
        qtbot.wait(500)


def test_non_click_tray_gestures_do_not_open_the_app(qtbot: QtBot) -> None:
    """Middle click and context-menu reasons are not "open the app"."""
    manager = TrayManager()

    with qtbot.assertNotEmitted(manager.open_dashboard):
        manager.handle_activated(QSystemTrayIcon.ActivationReason.MiddleClick)
        manager.handle_activated(QSystemTrayIcon.ActivationReason.Context)
        qtbot.wait(500)


def test_hiding_the_tray_cancels_a_pending_open(qtbot: QtBot) -> None:
    """A click landing just before Exit must not re-open the window."""
    manager = TrayManager()
    manager.handle_activated(QSystemTrayIcon.ActivationReason.Trigger)

    manager.hide()

    with qtbot.assertNotEmitted(manager.open_dashboard):
        qtbot.wait(500)
