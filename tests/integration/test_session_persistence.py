"""Integration tests for Phase 5 work-session engine persistence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile

import pytest
from app.core.clock import Clock
from app.domain.sessions.session import WorkSessionStatus
from app.domain.sessions.state_machine import AppState, SessionAction
from app.infrastructure.database.db import Database
from app.infrastructure.database.migrations import migrate
from app.infrastructure.database.repositories import SessionRepository, UserRepository
from app.services.session_service import SessionService
from sqlalchemy import create_engine

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


def make_service_with_user(
    db_path: Path, clock: Clock
) -> tuple[SessionService, Database, int]:
    """Create a Service bound to db_path with a real user row."""
    db = Database(db_path)
    # ensure user exists
    with db.session() as s:
        user = UserRepository(s).upsert(
            external_user_id="ext-test",
            email="employee@example.com",
            display_name="Test User",
            workspace_name="Engineering",
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
    service1, _db1, uid = make_service_with_user(db_path, clock1)
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
    service2 = SessionService(
        clock=resumed_clock, session_factory=Database(db_path).session
    )
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


# ── Event-driven sync nudge ────────────────────────────────────────────────
def test_each_user_action_requests_a_sync(temp_database):
    """Check in, break, resume and check out each nudge the worker once.

    The poll timer would still deliver these, but only up to a poll interval
    later, and these four are exactly the events an observer watches.
    """
    _, db_path = temp_database
    clock = FixedClock(T0)
    service, _db, _uid = make_service_with_user(db_path, clock)
    requested: list[int] = []
    service.set_sync_notifier(lambda: requested.append(1))

    service.machine.apply(SessionAction.LOGIN, at=T0)
    service.check_in()
    assert len(requested) == 1, "check-in requests a drain"

    clock.advance(300)
    service.take_break()
    assert len(requested) == 2, "break requests a drain"

    clock.advance(600)
    service.resume()
    assert len(requested) == 3, "resume requests a drain"

    clock.advance(300)
    service.check_out()
    assert len(requested) == 4, "check-out requests a drain"


def test_sync_is_requested_only_after_the_row_is_committed(temp_database):
    """The worker must never be pointed at a drain it cannot yet read."""
    _, db_path = temp_database
    clock = FixedClock(T0)
    service, _db, uid = make_service_with_user(db_path, clock)
    service.machine.apply(SessionAction.LOGIN, at=T0)

    rows_visible_at_request: list[int] = []

    def on_request() -> None:
        # Read through a *separate* connection, exactly as the sync worker
        # would. If the request fired before the commit, this sees nothing.
        with Database(db_path).session() as s:
            rows_visible_at_request.append(len(SessionRepository(s).list_for_user(uid)))

    service.set_sync_notifier(on_request)
    service.check_in()

    assert rows_visible_at_request == [1], (
        "the work session row is readable when the nudge fires"
    )


def test_a_failing_nudge_does_not_break_the_user_action(temp_database):
    """A nudge is an optimisation; losing it must not lose the check-in."""
    _, db_path = temp_database
    clock = FixedClock(T0)
    service, db, uid = make_service_with_user(db_path, clock)
    service.machine.apply(SessionAction.LOGIN, at=T0)

    def boom() -> None:
        raise RuntimeError("worker is gone")

    service.set_sync_notifier(boom)
    view = service.check_in()

    assert view.state is AppState.WORKING
    with db.session() as s:
        sessions = SessionRepository(s).list_for_user(uid)
        assert len(sessions) == 1
        assert sessions[0].status == WorkSessionStatus.WORKING.value


def test_no_nudge_when_nothing_was_persisted():
    """Without a database there is nothing queued, so nothing to drain."""
    service = SessionService()
    service.machine.apply(SessionAction.LOGIN, at=T0)
    service.set_user(user_id=42)
    requested: list[int] = []
    service.set_sync_notifier(lambda: requested.append(1))

    service.check_in()

    assert requested == []


def test_a_check_in_reaches_the_provider_without_waiting_for_the_poll(temp_database):
    """End-to-end: click -> enqueue -> nudge -> drain -> provider.

    This is the whole point of the change, so it is worth asserting the chain
    rather than each link: with the poll timer never created, a single check-in
    must still reach the backend on the strength of the nudge alone.
    """
    from app.infrastructure.network.sync_adapter import DummySyncProvider
    from app.services.sync_service import SyncService
    from app.workers.sync_worker import SyncWorker

    _, db_path = temp_database
    clock = FixedClock(T0)
    service, db, _uid = make_service_with_user(db_path, clock)
    service.machine.apply(SessionAction.LOGIN, at=T0)

    provider = DummySyncProvider()
    worker = SyncWorker(
        SyncService(provider=provider, session_factory=db.session),
        workspace_provider=None,
        policy_service=None,
    )
    # `_running` is set directly rather than via start(), so `worker._timer`
    # stays None and the poll path is structurally unable to fire here.
    worker._running = True
    assert worker._timer is None
    service.set_sync_notifier(worker.request_sync)

    service.check_in()

    # The nudge armed the debounce; no request has gone out yet.
    assert provider.call_count == 0

    # Stand in for the debounce elapsing.
    worker._tick()

    assert provider.call_count == 1, (
        "the check-in reached the backend via the nudge alone"
    )
    assert [i["entity_type"] for i in provider.synced_items] == ["work_session"]
