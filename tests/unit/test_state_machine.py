"""Unit tests for the session state machine (docs/STATE_MACHINE.md).

Verifies that invalid transitions are *rejected by the machine itself* —
not merely prevented by UI flow.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.core.exceptions import SessionStateError
from app.domain.sessions.state_machine import (
    AppState,
    SessionAction,
    SessionMachine,
    is_valid,
)

AT = datetime(2026, 9, 23, 8, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("state", "action", "expected"),
    [
        (AppState.LOGGED_OUT, SessionAction.LOGIN, AppState.READY),
        (AppState.READY, SessionAction.CHECK_IN, AppState.WORKING),
        (AppState.READY, SessionAction.LOGOUT, AppState.LOGGED_OUT),
        (AppState.WORKING, SessionAction.CHECK_OUT, AppState.COMPLETED),
        (AppState.WORKING, SessionAction.TAKE_BREAK, AppState.BREAK),
        (AppState.BREAK, SessionAction.RESUME, AppState.WORKING),
        (AppState.BREAK, SessionAction.CHECK_OUT, AppState.COMPLETED),
        (AppState.COMPLETED, SessionAction.CHECK_IN, AppState.WORKING),
        (AppState.COMPLETED, SessionAction.LOGOUT, AppState.LOGGED_OUT),
    ],
)
def test_valid_transitions(
    state: AppState, action: SessionAction, expected: AppState
) -> None:
    machine = SessionMachine(state=state)
    assert machine.can(action)
    assert machine.next_state(action) is expected
    assert is_valid(state, action)


@pytest.mark.parametrize(
    ("state", "action"),
    [
        (AppState.LOGGED_OUT, SessionAction.CHECK_OUT),
        (AppState.LOGGED_OUT, SessionAction.CHECK_IN),
        (AppState.LOGGED_OUT, SessionAction.TAKE_BREAK),
        (AppState.LOGGED_OUT, SessionAction.RESUME),
        (AppState.READY, SessionAction.CHECK_OUT),
        (AppState.READY, SessionAction.TAKE_BREAK),
        (AppState.READY, SessionAction.RESUME),
        (AppState.WORKING, SessionAction.LOGIN),
        (AppState.WORKING, SessionAction.CHECK_IN),
        (AppState.WORKING, SessionAction.RESUME),
        (AppState.BREAK, SessionAction.LOGIN),
        (AppState.BREAK, SessionAction.CHECK_IN),
        (AppState.BREAK, SessionAction.TAKE_BREAK),
        (AppState.COMPLETED, SessionAction.CHECK_OUT),
        (AppState.COMPLETED, SessionAction.TAKE_BREAK),
        (AppState.COMPLETED, SessionAction.RESUME),
    ],
)
def test_invalid_transitions_are_rejected(
    state: AppState, action: SessionAction
) -> None:
    machine = SessionMachine(state=state)
    assert not machine.can(action)
    assert not is_valid(state, action)
    with pytest.raises(SessionStateError):
        machine.next_state(action)


def test_check_in_requires_logged_in_user() -> None:
    machine = SessionMachine(state=AppState.READY)
    with pytest.raises(SessionStateError):
        machine.apply(SessionAction.CHECK_IN, at=AT)


def test_full_happy_path_flow() -> None:
    machine = SessionMachine()
    machine.apply(SessionAction.LOGIN, at=AT)
    assert machine.state is AppState.READY

    machine.apply(SessionAction.CHECK_IN, at=AT, user_id=7)
    assert machine.state is AppState.WORKING
    assert machine.session is not None
    assert machine.session.user_id == 7

    machine.apply(SessionAction.TAKE_BREAK, at=AT)
    assert machine.state is AppState.BREAK
    assert machine.session.breaks

    machine.apply(SessionAction.RESUME, at=AT)
    assert machine.state is AppState.WORKING
    assert machine.session.breaks[-1].is_open is False

    machine.apply(SessionAction.CHECK_OUT, at=AT)
    assert machine.state is AppState.COMPLETED
    assert machine.session.total_work_seconds >= 0


def test_check_in_after_checkout_starts_new_distinct_session() -> None:
    machine = SessionMachine(state=AppState.COMPLETED)
    machine.apply(SessionAction.CHECK_IN, at=AT, user_id=3)
    assert machine.state is AppState.WORKING


def test_restore_unfinished_session() -> None:
    from app.domain.sessions.session import WorkSession, WorkSessionStatus

    machine = SessionMachine()
    restored = WorkSession(
        user_id=5,
        started_at=AT,
        status=WorkSessionStatus.WORKING,
        id=42,
    )
    assert machine.restore(restored) is AppState.WORKING
    assert machine.can(SessionAction.CHECK_OUT)


def test_available_actions_tracks_state() -> None:
    machine = SessionMachine(state=AppState.WORKING)
    assert SessionAction.TAKE_BREAK in machine.available_actions()
    assert SessionAction.CHECK_OUT in machine.available_actions()
    assert SessionAction.CHECK_IN not in machine.available_actions()
