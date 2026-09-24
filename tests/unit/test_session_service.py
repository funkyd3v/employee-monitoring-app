"""Unit tests for :mod:`app.services.session_service`.

The service is the UI's only seam to the work session — it must expose
correct, recomputed projections (never accumulated memory) and drive the
shared state machine on behalf of the dashboard/tray.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.core.clock import Clock
from app.core.exceptions import SessionStateError
from app.domain.activity.activity import ActivityState
from app.domain.sessions.state_machine import AppState, SessionAction
from app.services.session_service import SessionService


class FixedClock(Clock):
    """A clock whose UTC source is a steppable :class:`datetime` state."""

    def __init__(self, start: datetime) -> None:
        self._now = start
        self._mono = 0.0
        super().__init__(utc=self._read, monotonic=self._read_mono)

    def _read(self) -> datetime:
        return self._now

    def _read_mono(self) -> float:
        return self._mono

    def advance(self, seconds: int) -> None:
        self._now = self._now + timedelta(seconds=seconds)
        self._mono += seconds


def make_service(now: datetime) -> tuple[SessionService, FixedClock]:
    clock = FixedClock(now)
    service = SessionService(clock=clock)
    # Mirrors the real flow (controller after AuthService.login): the shared
    # machine reaches READY via LOGIN, then the session service binds the user.
    service.machine.apply(SessionAction.LOGIN, at=now)
    service.set_user(user_id=42)
    return service, clock


# ── Initial state ─────────────────────────────────────────────────────────
def test_initial_view_is_logged_out() -> None:
    service = SessionService()
    service.set_user(user_id=42)
    view = service.tick()

    assert view.state is AppState.LOGGED_OUT
    assert view.ticking is False
    assert view.can_check_in is False


# ── READY (post-login, checked-out) ────────────────────────────────────────
def test_ready_after_login_can_check_in() -> None:
    service, _ = make_service(datetime(2026, 1, 5, 9, 0, tzinfo=UTC))
    view = service.tick()

    assert view.state is AppState.READY
    assert view.ticking is False
    assert view.can_check_in is True
    assert view.can_take_break is False


def test_check_in_requires_authenticated_user() -> None:
    service = SessionService()
    with pytest.raises(SessionStateError, match="authenticated"):
        service.check_in()


# ── WORKING ────────────────────────────────────────────────────────────────
def test_check_in_starts_working_view() -> None:
    start = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
    service, _ = make_service(start)

    view = service.check_in()

    assert view.state is AppState.WORKING
    assert view.ticking is True
    assert view.elapsed_work_seconds == 0
    assert view.can_take_break is True
    assert view.can_check_out is True
    assert view.can_check_in is False


def test_elapsed_work_time_is_recomputed_not_accumulated() -> None:
    start = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
    service, clock = make_service(start)
    service.check_in()

    clock.advance(95)  # 1m35s of work
    first = service.tick()
    assert first.elapsed_work_seconds == 95

    # A second projection starts from timestamps, not memory.
    clock.advance(30)
    second = service.tick()
    assert second.elapsed_work_seconds == 125


# ── BREAK ──────────────────────────────────────────────────────────────────
def test_take_break_shows_open_break_duration() -> None:
    start = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
    service, clock = make_service(start)
    service.check_in()

    service.take_break()
    view = service.tick()
    assert view.state is AppState.BREAK
    assert view.can_resume is True
    assert view.elapsed_break_seconds == 0

    clock.advance(60)
    view = service.tick()
    assert view.elapsed_break_seconds == 60
    assert view.elapsed_work_seconds == 60  # work frozen during the break


def test_resume_returns_to_working_and_reopens_ticker() -> None:
    start = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
    service, clock = make_service(start)
    service.check_in()
    service.take_break()
    clock.advance(120)

    view = service.resume()
    assert view.state is AppState.WORKING
    assert view.ticking is True


# ── COMPLETED ──────────────────────────────────────────────────────────────
def test_check_out_ends_session_with_total_time() -> None:
    start = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
    service, clock = make_service(start)
    service.check_in()
    clock.advance(600)  # 10m work
    service.take_break()
    clock.advance(300)  # 5m break
    service.resume()
    clock.advance(600)  # another 10m work

    view = service.check_out()

    assert view.state is AppState.COMPLETED
    assert view.ticking is False
    assert view.elapsed_work_seconds == 1200  # 10m + 10m
    assert view.break_minutes == 5
    assert view.active_minutes == 20
    assert view.checked_out_at is not None
    assert view.can_check_in is True


def test_check_out_while_working_without_break_counts_work_only() -> None:
    start = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
    service, clock = make_service(start)
    service.check_in()
    clock.advance(870)  # 14m30s

    view = service.check_out()
    assert view.state is AppState.COMPLETED
    assert view.active_minutes == 14
    assert view.break_minutes == 0


def test_recheck_in_after_completed_keeps_summary() -> None:
    start = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
    service, clock = make_service(start)
    service.check_in()
    clock.advance(60)
    service.check_out()

    view = service.check_in()
    assert view.state is AppState.WORKING
    assert view.elapsed_work_seconds == 0  # a distinct, fresh session


# ── Machine rejection is surfaced, not papered over ─────────────────────────
def test_invalid_actions_raise_session_state_error() -> None:
    service, _ = make_service(datetime(2026, 1, 5, 9, 0, tzinfo=UTC))

    with pytest.raises(SessionStateError):
        service.take_break()  # no session yet
    with pytest.raises(SessionStateError):
        service.resume()
    with pytest.raises(SessionStateError):
        service.check_out()


# ── Projection defaults for future phases ──────────────────────────────────
def test_active_view_defaults_activity_state() -> None:
    start = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
    service, _ = make_service(start)
    service.check_in()

    view = service.tick()
    assert view.activity_state is ActivityState.ACTIVE  # Phase 6 fills this


def test_machine_is_shared_instance() -> None:
    service, _ = make_service(datetime(2026, 1, 5, 9, 0, tzinfo=UTC))
    # The shared machine (driven by auth) governs SessionService capability.
    assert service.can(SessionAction.CHECK_IN) is True  # was brought READY
    assert service.can(SessionAction.CHECK_OUT) is False
