"""Tests for the system tray (docs/UI_SPEC.md §System tray).

``tray_state_for`` maps projections to icon states and is pure; the
:class:`TrayManager` enables/disables actions from the same projection so the
tray can never offer an action the machine rejects.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from app.domain.activity.activity import ActivityState
from app.domain.sessions.state_machine import AppState
from app.services.session_service import SessionView
from app.ui.tray.tray_manager import TrayManager, TrayState, tray_state_for

if TYPE_CHECKING:
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
