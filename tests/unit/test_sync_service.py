"""Unit tests for SyncService — drain, backoff, deletion rule, recovery."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from app.domain.sync.provider import ConnectivityState
from app.domain.sync.sync import SyncStatus
from app.infrastructure.database.db import Database
from app.infrastructure.database.migrations import migrate
from app.infrastructure.database.models import ScreenshotMetadata, SyncQueueItem
from app.infrastructure.database.repositories import (
    ScreenshotRepository,
    SessionRepository,
    SyncQueueRepository,
    UserRepository,
)
from app.infrastructure.network.sync_adapter import DummySyncProvider
from app.services.sync_service import SyncService

if TYPE_CHECKING:
    from pathlib import Path

T0 = datetime(2026, 9, 23, 10, 0, tzinfo=UTC)


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "sync_test.db")
    migrate(database.engine)
    yield database
    database.dispose()


def _make_user_and_session(db: Database) -> int:
    with db.session() as s:
        u_repo = UserRepository(s)
        sess_repo = SessionRepository(s)
        user = u_repo.upsert(
            external_user_id="ext-1",
            email="a@example.com",
            display_name="A",
            team_name="T",
        )
        s.flush()
        ws = sess_repo.create(user_id=user.id, started_at=T0)
        s.commit()
        return ws.id


def test_sync_queue_success_deletes_only_after_confirmed(
    tmp_path: Path, db: Database
) -> None:
    ws_id = _make_user_and_session(db)
    # Enqueue a work_session item
    with db.session() as s:
        q = SyncQueueRepository(s)
        item = q.enqueue(
            entity_type="work_session", entity_id=ws_id, operation="CREATE"
        )
        s.commit()
        item_id = item.id

    provider = DummySyncProvider()
    svc = SyncService(provider=provider, session_factory=db.session)
    result = svc.sync_once(at=T0)
    assert result["synced"] == 1
    # Row should be deleted (confirm-then-delete)
    with db.session() as s:
        assert s.get(SyncQueueItem, item_id) is None


def test_sync_screenshot_upload_success(tmp_path: Path, db: Database) -> None:
    ws_id = _make_user_and_session(db)
    # Create a real file
    pending = tmp_path / "pending"
    pending.mkdir()
    file_path = pending / "shot.jpg"
    file_path.write_bytes(b"fake-jpeg-data" * 100)

    from app.domain.activity.activity import ActivityState as AS

    with db.session() as s:
        repo2 = ScreenshotRepository(s)
        shot = repo2.add(
            session_id=ws_id,
            captured_at=T0,
            activity_state=AS.ACTIVE,
            file_path=str(file_path),
            file_size=file_path.stat().st_size,
            checksum="abc",
        )
        s.commit()
        shot_id = shot.id

    provider = DummySyncProvider()
    svc = SyncService(provider=provider, session_factory=db.session)
    result = svc.sync_once(at=T0)
    assert result["synced"] == 1
    with db.session() as s:
        row = s.get(ScreenshotMetadata, shot_id)
        assert row is not None
        assert row.sync_status == SyncStatus.SYNCED.value
        assert row.attempt_count == 1
        assert len(provider.uploaded_screenshots) == 1


def test_offline_preserves_and_does_not_delete(tmp_path: Path, db: Database) -> None:
    ws_id = _make_user_and_session(db)
    with db.session() as s:
        q = SyncQueueRepository(s)
        item = q.enqueue(
            entity_type="work_session", entity_id=ws_id, operation="CREATE"
        )
        s.commit()
        item_id = item.id

    provider = DummySyncProvider(connectivity=ConnectivityState.OFFLINE)
    svc = SyncService(provider=provider, session_factory=db.session)
    result = svc.sync_once(at=T0)
    assert result["skipped_offline"] == 1
    assert result["synced"] == 0
    # Data must be preserved
    with db.session() as s:
        assert s.get(SyncQueueItem, item_id) is not None
        row = s.get(SyncQueueItem, item_id)
        assert row is not None
        assert row.status in (SyncStatus.PENDING.value, SyncStatus.FAILED.value)


def test_retry_backoff_respected(tmp_path: Path, db: Database) -> None:
    ws_id = _make_user_and_session(db)
    with db.session() as s:
        q = SyncQueueRepository(s)
        item = q.enqueue(
            entity_type="work_session", entity_id=ws_id, operation="CREATE"
        )
        s.commit()
        item_id = item.id

    provider = DummySyncProvider(fail_next=1)
    svc = SyncService(provider=provider, session_factory=db.session)
    # First attempt fails (injected), attempt_count becomes 1
    result1 = svc.sync_once(at=T0)
    assert result1["failed"] == 1
    with db.session() as s:
        row = s.get(SyncQueueItem, item_id)
        assert row is not None
        assert row.attempt_count == 1

    # Immediate retry should be skipped due to backoff (30s)
    provider.set_fail_next(0)  # next would succeed if attempted
    result2 = svc.sync_once(at=T0 + timedelta(seconds=10))
    assert result2["skipped_backoff"] >= 1
    assert result2["synced"] == 0

    # After 30s, due
    result3 = svc.sync_once(at=T0 + timedelta(seconds=31))
    assert result3["synced"] == 1
    with db.session() as s:
        assert s.get(SyncQueueItem, item_id) is None


def test_stale_recovery_resets_syncing(tmp_path: Path, db: Database) -> None:
    ws_id = _make_user_and_session(db)
    with db.session() as s:
        q = SyncQueueRepository(s)
        item = q.enqueue(
            entity_type="work_session", entity_id=ws_id, operation="CREATE"
        )
        q.mark_sync_started(item.id)
        s.commit()
        # Also screenshot stale
        from app.domain.activity.activity import ActivityState as AS

        file_path = tmp_path / "shot.jpg"
        file_path.write_bytes(b"data")
        s_repo = ScreenshotRepository(s)
        shot = s_repo.add(
            session_id=ws_id,
            captured_at=T0,
            activity_state=AS.ACTIVE,
            file_path=str(file_path),
        )
        s_repo.mark_sync_started(shot.id)
        s.commit()

    provider = DummySyncProvider()
    svc = SyncService(provider=provider, session_factory=db.session)
    stale = svc.recover_stale()
    assert stale["queue_reset"] == 1
    assert stale["screenshots_reset"] == 1

    # Now sync should succeed
    result = svc.sync_once(at=T0)
    # Both queue and screenshot will be attempted; at least queue should sync
    assert result["synced"] >= 1


def test_orphan_queue_removed(tmp_path: Path, db: Database) -> None:
    # Enqueue for non-existent entity
    with db.session() as s:
        q = SyncQueueRepository(s)
        q.enqueue(entity_type="work_session", entity_id=99999, operation="CREATE")
        q.enqueue(entity_type="break", entity_id=88888, operation="CREATE")
        s.commit()

    provider = DummySyncProvider()
    svc = SyncService(provider=provider, session_factory=db.session)
    result = svc.recover_orphans()
    assert result["orphan_queue_removed"] == 2
    with db.session() as s:
        from sqlalchemy import select

        rows = list(s.scalars(select(SyncQueueItem)))
        assert len(rows) == 0


def test_failed_preserves_data_never_deletes_on_attempt(
    tmp_path: Path, db: Database
) -> None:
    ws_id = _make_user_and_session(db)
    with db.session() as s:
        q = SyncQueueRepository(s)
        item = q.enqueue(
            entity_type="work_session", entity_id=ws_id, operation="CREATE"
        )
        s.commit()
        item_id = item.id

    provider = DummySyncProvider(always_fail=True)
    svc = SyncService(provider=provider, session_factory=db.session)
    for i in range(3):
        svc.sync_once(at=T0 + timedelta(seconds=i * 40))

    with db.session() as s:
        row = s.get(SyncQueueItem, item_id)
        assert row is not None  # never deleted
        assert row.status == SyncStatus.FAILED.value
        assert row.attempt_count >= 1
