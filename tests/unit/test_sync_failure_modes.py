"""Failure-mode tests for sync/cleanup (docs/TESTING_AND_DOD.md §Failure tests)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.domain.sync.sync import SyncStatus
from app.infrastructure.database.db import Database
from app.infrastructure.database.migrations import migrate
from app.infrastructure.database.models import SyncQueueItem
from app.infrastructure.database.repositories import SyncQueueRepository
from app.infrastructure.network.sync_adapter import DummySyncProvider
from app.services.cleanup_service import CleanupService
from app.services.sync_service import SyncService

if TYPE_CHECKING:
    from pathlib import Path

T0 = datetime(2026, 9, 23, 10, 0, tzinfo=UTC)


def test_sync_handles_provider_exception_preserves_data(tmp_path: Path) -> None:
    """Provider raising should not delete local data."""
    db = Database(tmp_path / "fail1.db")
    migrate(db.engine)
    try:
        with db.session() as s:
            q = SyncQueueRepository(s)
            item = q.enqueue(
                entity_type="work_session", entity_id=1, operation="CREATE"
            )
            s.commit()
            item_id = item.id

        class ExplodingProvider(DummySyncProvider):
            def sync_item(self, **kw):  # type: ignore[no-untyped-def]
                raise RuntimeError("network boom")

        provider = ExplodingProvider()
        svc = SyncService(provider=provider, session_factory=db.session)
        result = svc.sync_once(at=T0)
        assert result["failed"] == 1
        with db.session() as s:
            assert s.get(SyncQueueItem, item_id) is not None
    finally:
        db.dispose()


def test_sync_handles_db_unavailable(tmp_path: Path) -> None:
    """DB factory raising should not crash sync."""

    def exploding_factory():  # type: ignore[no-untyped-def]
        raise RuntimeError("db locked")

    provider = DummySyncProvider()
    svc = SyncService(provider=provider, session_factory=exploding_factory)  # type: ignore[arg-type]
    # Should return gracefully, not raise
    result = svc.sync_once(at=T0)
    assert result["synced"] == 0


def test_corrupt_screenshot_file_handling(tmp_path: Path) -> None:
    """Screenshot file missing or empty should be marked FAILED, not deleted queue blindly."""
    db = Database(tmp_path / "fail2.db")
    migrate(db.engine)
    try:
        from app.domain.activity.activity import ActivityState
        from app.infrastructure.database.repositories import (
            ScreenshotRepository,
            SessionRepository,
            UserRepository,
        )

        with db.session() as s:
            u_repo = UserRepository(s)
            user = u_repo.upsert(
                external_user_id="ext",
                email="a@example.com",
                display_name="A",
                team_name="T",
            )
            s.flush()
            ws = SessionRepository(s).create(user_id=user.id, started_at=T0)
            s.commit()
            ws_id = ws.id

        # Missing file path
        missing = tmp_path / "missing.jpg"
        with db.session() as s:
            repo = ScreenshotRepository(s)
            shot = repo.add(
                session_id=ws_id,
                captured_at=T0,
                activity_state=ActivityState.ACTIVE,
                file_path=str(missing),
            )
            s.commit()
            shot_id = shot.id

        provider = DummySyncProvider()
        svc = SyncService(provider=provider, session_factory=db.session)
        result = svc.sync_once(at=T0)
        assert result["failed"] == 1
        with db.session() as s:
            s.get(SyncQueueItem, shot_id)  # queue may be empty, check screenshot state
            # Actually screenshot failure marks FAILED not deleted
            from app.infrastructure.database.models import ScreenshotMetadata

            shot_row = s.get(ScreenshotMetadata, shot_id)
            assert shot_row is not None
            assert shot_row.sync_status == SyncStatus.FAILED.value
            assert shot_row.attempt_count == 1
    finally:
        db.dispose()


def test_duplicate_sync_idempotent(tmp_path: Path) -> None:
    """Syncing same item twice after confirmed should be no-op (deleted)."""
    db = Database(tmp_path / "dup.db")
    migrate(db.engine)
    try:
        with db.session() as s:
            q = SyncQueueRepository(s)
            item = q.enqueue(
                entity_type="work_session", entity_id=1, operation="CREATE"
            )
            s.commit()
            item_id = item.id

        provider = DummySyncProvider()
        svc = SyncService(provider=provider, session_factory=db.session)
        svc.sync_once(at=T0)
        with db.session() as s:
            assert s.get(SyncQueueItem, item_id) is None

        # Second sync should not resurrect or duplicate
        result2 = svc.sync_once(at=T0)
        assert result2["synced"] == 0
        assert provider.call_count == 1
    finally:
        db.dispose()


def test_cleanup_handles_missing_file_gracefully(tmp_path: Path) -> None:
    """Cleanup should not crash when file already gone."""
    db = Database(tmp_path / "clean_fail.db")
    migrate(db.engine)
    try:
        from app.domain.activity.activity import ActivityState
        from app.infrastructure.database.repositories import (
            ScreenshotRepository,
            SessionRepository,
            UserRepository,
        )

        with db.session() as s:
            user = UserRepository(s).upsert(
                external_user_id="ext",
                email="a@example.com",
                display_name="A",
                team_name="T",
            )
            s.flush()
            ws = SessionRepository(s).create(user_id=user.id, started_at=T0)
            s.commit()
            ws_id = ws.id

        ghost = tmp_path / "ghost.jpg"
        with db.session() as s:
            repo = ScreenshotRepository(s)
            shot = repo.add(
                session_id=ws_id,
                captured_at=T0,
                activity_state=ActivityState.ACTIVE,
                file_path=str(ghost),
            )
            repo.mark_synced(shot.id, synced_at=T0)
            s.commit()

        svc = CleanupService(
            session_factory=db.session, data_dir=tmp_path, retention_days=0
        )
        result = svc.run_once(at=T0)
        # File already missing, should not raise, deleted_files may be 0
        assert result["deleted_files"] == 0
    finally:
        db.dispose()
