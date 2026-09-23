"""Unit tests for WorkSession duration math (docs/ENGINEERING_RULES.md
§Timer Correctness) — elapsed work time is recomputed from persisted
timestamps, never accumulated in memory."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.sessions.session import Break, WorkSession, WorkSessionStatus

START = datetime(2026, 9, 23, 9, 0, tzinfo=UTC)
NOW = START + timedelta(hours=2)


def test_no_breaks_elapsed_equals_wall_span() -> None:
    session = WorkSession(user_id=1, started_at=START)
    assert session.elapsed_work_seconds(NOW) == 2 * 3600


def test_breaks_subtracted_from_elapsed() -> None:
    session = WorkSession(user_id=1, started_at=START)
    session.breaks.append(
        Break(
            session_id=0,
            started_at=START + timedelta(minutes=30),
            ended_at=START + timedelta(minutes=45),
            duration_seconds=900,
        )
    )
    assert session.accumulated_break_seconds == 900
    assert session.elapsed_work_seconds(NOW) == 2 * 3600 - 900


def test_checkout_freezes_total_and_ignores_later_now() -> None:
    session = WorkSession(user_id=1, started_at=START)
    session.breaks.append(
        Break(
            session_id=0,
            started_at=START + timedelta(hours=1),
            ended_at=START + timedelta(hours=1, minutes=10),
            duration_seconds=600,
        )
    )
    session.checkout(NOW)
    assert session.status is WorkSessionStatus.COMPLETED
    frozen = session.total_work_seconds
    assert frozen == 2 * 3600 - 600
    later = NOW + timedelta(hours=5)
    assert session.elapsed_work_seconds(later) == frozen


def test_elapsed_is_never_negative_after_sleep_freeze() -> None:
    session = WorkSession(user_id=1, started_at=START)
    assert session.elapsed_work_seconds(START + timedelta(seconds=-30)) == 0


def test_break_close_computes_duration_from_persisted_start() -> None:
    brk = Break(session_id=0, started_at=START)
    closed = brk.close(START + timedelta(minutes=5))
    assert closed.duration_seconds == 300
    assert closed.is_open is False


def test_break_cannot_close_twice() -> None:
    brk = Break(session_id=0, started_at=START)
    closed = brk.close(START + timedelta(minutes=1))
    try:
        closed.close(START + timedelta(minutes=2))
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("closing an already-closed break must raise")


def test_create_session_factory() -> None:
    session = WorkSession(user_id=2, started_at=START)
    assert session.status is WorkSessionStatus.WORKING
    assert session.is_active
    assert not session.breaks
