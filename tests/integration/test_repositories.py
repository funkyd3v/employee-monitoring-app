"""Integration tests for repositories sitting on a real (temporary) SQLite DB.

Covers docs/DATA_MODEL.md table access and round-trips; the data-deletion
rule (delete only after server-confirmed persistence) is exercised at the
sync-queue level.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from app.core.clock import utc_now
from app.domain.activity.activity import ActivityState
from app.domain.sessions.session import WorkSessionStatus
from app.domain.sync.sync import SyncStatus
from app.infrastructure.database.repositories import (
    ActivityRepository,
    AppStateRepository,
    BreakRepository,
    ScreenshotRepository,
    SessionRepository,
    SettingsRepository,
    SyncQueueRepository,
    UserRepository,
)
from sqlalchemy.exc import IntegrityError

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session

T0 = datetime(2026, 9, 23, 9, 0, tzinfo=UTC)


@pytest.fixture
def session(database) -> Session:
    """Fresh Session per test (each test has its own migrated DB)."""
    return database.session()


@pytest.fixture
def users(session: Session) -> UserRepository:
    return UserRepository(session)


@pytest.fixture
def sessions(session: Session) -> SessionRepository:
    return SessionRepository(session)


@pytest.fixture
def breaks(session: Session) -> BreakRepository:
    return BreakRepository(session)


@pytest.fixture
def activity(session: Session) -> ActivityRepository:
    return ActivityRepository(session)


@pytest.fixture
def screenshots(session: Session) -> ScreenshotRepository:
    return ScreenshotRepository(session)


@pytest.fixture
def queue(session: Session) -> SyncQueueRepository:
    return SyncQueueRepository(session)


@pytest.fixture
def settings(session: Session) -> SettingsRepository:
    return SettingsRepository(session)


@pytest.fixture
def app_state(session: Session) -> AppStateRepository:
    return AppStateRepository(session)


def _make_user(users: UserRepository, email: str = "a@example.com"):
    return users.upsert(
        external_user_id="ext-1",
        email=email,
        display_name="Alice",
        team_name="Engineering",
    )


def _make_session(sessions: SessionRepository, user_id: int) -> int:
    return sessions.create(user_id=user_id, started_at=T0).id


class TestUserRepository:
    def test_upsert_inserts_then_updates_same_row(
        self, session: Session, users: UserRepository
    ) -> None:
        first = _make_user(users)
        session.commit()
        sid = first.id
        again = _make_user(users, email="a@example.com")
        session.commit()
        assert again.id == sid  # same row updated, not duplicated
        assert again.team_name == "Engineering"

    def test_get_by_email_and_id(self, session: Session, users: UserRepository) -> None:
        row = _make_user(users)
        session.commit()
        assert users.get_by_email("a@example.com").id == row.id
        assert users.get_by_id(row.id).email == "a@example.com"


class TestSessionRepository:
    def test_create_and_get_active(
        self, session: Session, users: UserRepository, sessions: SessionRepository
    ) -> None:
        user = _make_user(users)
        session.commit()
        ws = sessions.create(user_id=user.id, started_at=T0)
        session.commit()
        active = sessions.get_active(user_id=user.id)
        assert active is not None
        assert active.id == ws.id
        assert active.status == WorkSessionStatus.WORKING.value
        assert active.started_at.tzinfo is not None  # tz preserved by UTCDateTime

    def test_two_sessions_active_returns_newest(
        self, session: Session, users: UserRepository, sessions: SessionRepository
    ) -> None:
        user = _make_user(users)
        session.commit()
        old = sessions.create(user_id=user.id, started_at=T0)
        new = sessions.create(user_id=user.id, started_at=T0 + timedelta(minutes=30))
        sessions.checkout(
            old.id, ended_at=T0 + timedelta(hours=1), total_work_seconds=3600
        )
        session.commit()
        assert sessions.get_active(user.id).id == new.id

    def test_checkout_sets_completed_and_totals(
        self, session: Session, users: UserRepository, sessions: SessionRepository
    ) -> None:
        user = _make_user(users)
        session.commit()
        ws = sessions.create(user_id=user.id, started_at=T0)
        sessions.checkout(
            ws.id, ended_at=T0 + timedelta(hours=2), total_work_seconds=7200
        )
        session.commit()
        done = sessions.get_by_id(ws.id)
        assert done.status == WorkSessionStatus.COMPLETED.value
        assert done.total_work_seconds == 7200
        assert done.ended_at == T0 + timedelta(hours=2)
        assert sessions.get_active(user.id) is None

    def test_list_for_user_descending(
        self, session: Session, users: UserRepository, sessions: SessionRepository
    ) -> None:
        user = _make_user(users)
        session.commit()
        for i in range(3):
            s = sessions.create(user_id=user.id, started_at=T0 + timedelta(days=i))
            sessions.checkout(
                s.id,
                ended_at=T0 + timedelta(days=i, hours=1),
                total_work_seconds=3600,
            )
        session.commit()
        listed = sessions.list_for_user(user.id)
        assert [ws.started_at for ws in listed] == sorted(
            [ws.started_at for ws in listed], reverse=True
        )


class TestBreakRepository:
    def test_start_and_end_computes_duration(
        self,
        session: Session,
        users: UserRepository,
        sessions: SessionRepository,
        breaks: BreakRepository,
    ) -> None:
        user = _make_user(users)
        session.commit()
        ws = sessions.create(user_id=user.id, started_at=T0)
        brk = breaks.start(session_id=ws.id, started_at=T0 + timedelta(hours=1))
        breaks.end_open(brk.id, ended_at=T0 + timedelta(hours=1, minutes=15))
        session.commit()
        assert breaks.accumulate_seconds(ws.id) == 900
        assert breaks.open_for_session(ws.id) is None

    def test_accumulate_ignores_open_break(
        self,
        session: Session,
        users: UserRepository,
        sessions: SessionRepository,
        breaks: BreakRepository,
    ) -> None:
        user = _make_user(users)
        session.commit()
        ws = sessions.create(user_id=user.id, started_at=T0)
        breaks.start(session_id=ws.id, started_at=T0 + timedelta(hours=1))
        session.commit()
        assert breaks.accumulate_seconds(ws.id) == 0


class TestActivityRepository:
    def test_append_and_close_open(
        self,
        session: Session,
        users: UserRepository,
        sessions: SessionRepository,
        activity: ActivityRepository,
    ) -> None:
        user = _make_user(users)
        session.commit()
        ws = sessions.create(user_id=user.id, started_at=T0)

        activity.append(
            session_id=ws.id,
            state=ActivityState.ACTIVE,
            started_at=T0,
        )
        session.commit()
        rows = activity.list_for_session(ws.id)
        assert len(rows) == 1
        assert rows[0].state == ActivityState.ACTIVE.value
        assert rows[0].duration_seconds == 0

        activity.close_open(ended_at=T0 + timedelta(hours=1))
        session.commit()
        closed = activity.list_for_session(ws.id)
        assert closed[0].ended_at == T0 + timedelta(hours=1)
        assert closed[0].duration_seconds == 3600


class TestScreenshotRepository:
    def test_add_pending_and_sync_flow(
        self,
        tmp_path: Path,
        session: Session,
        users: UserRepository,
        sessions: SessionRepository,
        screenshots: ScreenshotRepository,
    ) -> None:
        user = _make_user(users)
        session.commit()
        ws = sessions.create(user_id=user.id, started_at=T0)

        shots = screenshots.add(
            session_id=ws.id,
            captured_at=T0 + timedelta(minutes=5),
            activity_state=ActivityState.ACTIVE,
            file_path=str(tmp_path / "sess_1_shot.jpg"),
            file_size=12345,
            checksum="deadbeef",
        )
        session.commit()
        assert shots.sync_status == SyncStatus.PENDING.value

        pending = screenshots.pending_batch(limit=10)
        assert [p.id for p in pending] == [shots.id]

        at = utc_now()
        screenshots.record_attempt(shots.id, at=at)
        screenshots.mark_synced(shots.id, synced_at=at)
        session.commit()
        assert screenshots.get_by_id(shots.id).sync_status == SyncStatus.SYNCED.value
        assert screenshots.get_by_id(shots.id).attempt_count == 1

        # Duplicate file paths rejected by unique constraint.
        with pytest.raises(IntegrityError):
            screenshots.add(
                session_id=ws.id,
                captured_at=T0 + timedelta(minutes=6),
                activity_state=ActivityState.IDLE,
                file_path=str(tmp_path / "sess_1_shot.jpg"),
            )


class TestSyncQueueRepository:
    def test_pending_flow_and_delete_after_confirm(
        self, session: Session, queue: SyncQueueRepository
    ) -> None:
        item = queue.enqueue(
            entity_type="work_session", entity_id=5, operation="CREATE"
        )
        session.commit()

        queue.record_attempt(item.id, at=utc_now())
        queue.record_error(item.id, "connection refused")
        session.commit()
        failed = queue.pending_batch(limit=10)
        assert [x.id for x in failed] == [item.id]

        queue.mark_synced(item.id)
        session.commit()
        assert queue.pending_batch(limit=10) == []

        # Only after confirmed: row may be physically removed.
        queue.delete_after_confirmed(item.id)
        session.commit()
        assert queue.pending_batch(limit=10) == []


class TestSettingsRepository:
    def test_set_get_update(
        self, session: Session, settings: SettingsRepository
    ) -> None:
        assert settings.get("missing") is None
        settings.set("interval", "60")
        settings.set("threshold", "300")
        session.commit()
        assert settings.get("interval") == "60"
        assert settings.get_all() == {"interval": "60", "threshold": "300"}
        settings.set("interval", "120")
        session.commit()
        assert settings.get("interval") == "120"


class TestAppStateRepository:
    def test_flag_roundtrip(
        self, session: Session, app_state: AppStateRepository
    ) -> None:
        assert app_state.get("theme") is None
        app_state.set("theme", "dark")
        session.commit()
        assert app_state.get("theme") == "dark"
        app_state.set("theme", "light")
        session.commit()
        assert app_state.get_all() == {"theme": "light"}
