"""Integration tests for Phase 5 work-session engine persistence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile

import pytest
from sqlalchemy import create_engine

from app.core.clock import Clock
from app.domain.sessions.session import WorkSessionStatus
from app.domain.sessions.state_machine import AppState, SessionAction
from app.infrastructure.database.db import Database
from app.infrastructure.database.migrations import migrate
from app.infrastructure.database.repositories import SessionRepository, UserRepository
from app.services.session_service import SessionService

T0 = datetime(2026, 9, 23, 8, 0, tzinfo=UTC)
USER_ID_PLACEHOLDER = 0  # real id comes from DB upsert


class FixedClock(Clock):
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

    def set(self, dt: datetime) -> None:
        self._now = dt


@pytest.fixture
def temp_database():
    with NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        migrate(engine)
        yield engine, db_path
    finally:
        engine.dispose()
        if db_path.exists():
            db_path.unlink()


def make_service_with_user(db_path: Path, clock: Clock) -> tuple[SessionService, Database, int]:
    """Create a Service bound to db_path with a real user row."""
    db = Database(db_path)
    # ensure user exists
    with db.session() as s:
        user = UserRepository(s).upsert(
            external_user_id="ext-test",
            email="employee@example.com",
            display_name="Test User",
            team_name="Engineering",
        )
        s.commit()
        uid = user.id
    service = SessionService(clock=clock, session_factory=db.session)
    service.set_user(uid)
    return service, db, uid


def test_full_session_lifecycle_with_persistence(temp_database):
    _, db_path = temp_database
    clock = FixedClock(T0)
    service, db, uid = make_service_with_user(db_path, clock)

    service.machine.apply(SessionAction.LOGIN, at=T0)
    view = service.check_in()
    assert view.state is AppState.WORKING

    clock.advance(300)
    view = service.tick()
    assert view.elapsed_work_seconds == 300

    service.take_break()
    clock.advance(600)
    view = service.tick()
    assert view.state is AppState.BREAK
    assert view.elapsed_break_seconds == 600
    # domain counts wall minus closed breaks only; open break not yet subtracted
    assert view.elapsed_work_seconds == 900

    service.resume()
    clock.advance(300)
    view = service.tick()
    assert view.state is AppState.WORKING
    # after resume, 600s break is closed: wall 1200 - 600 = 600
    assert view.elapsed_work_seconds == 600

    clock.advance(300)
    view = service.check_out()
    assert view.state is AppState.COMPLETED
    # wall 1500 - break 600 = 900
    assert view.elapsed_work_seconds == 900
    assert view.break_minutes == 10

    with db.session() as s:
        repo = SessionRepository(s)
        assert repo.get_active(uid) is None
        sessions = repo.list_for_user(uid)
        assert len(sessions) == 1
        assert sessions[0].status == WorkSessionStatus.COMPLETED.value
        assert sessions[0].total_work_seconds == 900


def test_session_restoration_after_restart(temp_database):
    _, db_path = temp_database
    clock1 = FixedClock(T0)
    service1, db1, uid = make_service_with_user(db_path, clock1)
    service1.machine.apply(SessionAction.LOGIN, at=T0)
    service1.check_in()
    clock1.advance(300)

    # simulate restart — new service with same DB and same user
    clock2 = FixedClock(T0)
    # need to keep same uid; create new service but re-ensure user exists
    service2 = SessionService(clock=clock2, session_factory=Database(db_path).session)
    service2.set_user(uid)
    assert service2.restore_session() is True
    assert service2.state is AppState.WORKING
    assert service2.machine.session is not None
    assert service2.machine.session.user_id == uid
    assert service2.machine.session.status == WorkSessionStatus.WORKING

    clock2.set(T0)
    clock2.advance(300)
    view = service2.tick()
    assert view.elapsed_work_seconds == 300
    assert view.can_take_break is True
    assert view.can_check_out is True


def test_checkout_persists_total_and_break(temp_database):
    _, db_path = temp_database
    clock = FixedClock(T0)
    service, db, uid = make_service_with_user(db_path, clock)
    service.machine.apply(SessionAction.LOGIN, at=T0)
    service.check_in()
    clock.advance(600)
    # checkout at T0+10m
    checkout_at = T0 + timedelta(seconds=600)
    clock.set(checkout_at)
    service.check_out()

    with db.session() as s:
        repo = SessionRepository(s)
        rows = repo.list_for_user(uid)
        assert len(rows) == 1
        assert rows[0].status == WorkSessionStatus.COMPLETED.value
        assert rows[0].total_work_seconds == 600
        assert rows[0].ended_at == checkout_at


def test_multiple_sessions_persisted_correctly(temp_database):
    _, db_path = temp_database
    clock = FixedClock(T0)
    service, db, uid = make_service_with_user(db_path, clock)
    service.machine.apply(SessionAction.LOGIN, at=T0)
    service.check_in()
    clock.advance(600)
    service.check_out()

    # second session starts right after first checkout (still same clock time)
    service.check_in()
    clock.advance(300)
    service.check_out()

    with db.session() as s:
        repo = SessionRepository(s)
        rows = repo.list_for_user(uid, limit=10)
        assert len(rows) == 2
        # ordered by started_at desc, so newest first
        assert rows[0].total_work_seconds == 300
        assert rows[1].total_work_seconds == 600
        assert rows[0].status == WorkSessionStatus.COMPLETED.value
        assert rows[1].status == WorkSessionStatus.COMPLETED.value


def test_break_persisted_and_restored(temp_database):
    _, db_path = temp_database
    clock = FixedClock(T0)
    service, db, uid = make_service_with_user(db_path, clock)
    service.machine.apply(SessionAction.LOGIN, at=T0)
    service.check_in()
    clock.advance(60)
    service.take_break()
    # now in BREAK, persist should have created break row
    with db.session() as s:
        from app.infrastructure.database.repositories import BreakRepository

        # find the session id
        sess = SessionRepository(s).get_active(uid)
        assert sess is not None
        assert sess.status == WorkSessionStatus.BREAK.value
        breaks = BreakRepository(s).list_for_session(sess.id)
        assert len(breaks) == 1
        assert breaks[0].ended_at is None

    # restart while on break — advance clock so break duration >0
    resumed_clock = FixedClock(T0 + timedelta(seconds=120))
    service2 = SessionService(clock=resumed_clock, session_factory=Database(db_path).session)
    service2.set_user(uid)
    assert service2.restore_session()
    assert service2.state is AppState.BREAK
    service2.resume()
    with Database(db_path).session() as s2:
        from app.infrastructure.database.repositories import BreakRepository

        sess2 = SessionRepository(s2).get_active(uid)
        assert sess2 is not None
        assert sess2.status == WorkSessionStatus.WORKING.value
        breaks2 = BreakRepository(s2).list_for_session(sess2.id)
        assert len(breaks2) == 1
        assert breaks2[0].ended_at is not None
        assert breaks2[0].duration_seconds > 0
