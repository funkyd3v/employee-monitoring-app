"""Unit tests for CleanupService."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.domain.activity.activity import ActivityState
from app.domain.sync.sync import SyncStatus
from app.infrastructure.database.db import Database
from app.infrastructure.database.migrations import migrate
from app.infrastructure.database.models import ScreenshotMetadata, SyncQueueItem
from app.infrastructure.database.repositories import ScreenshotRepository, SyncQueueRepository, UserRepository, SessionRepository
from app.services.cleanup_service import CleanupService

T0 = datetime(2026, 9, 23, 10, 0, tzinfo=UTC)


def _make_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "cleanup_test.db")
    migrate(db.engine)
    return db


def test_cleanup_deletes_only_synced_queue(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    try:
        with db.session() as s:
            q = SyncQueueRepository(s)
            # One PENDING, one SYNCED
            pending = q.enqueue(entity_type="work_session", entity_id=1, operation="CREATE")
            synced = q.enqueue(entity_type="work_session", entity_id=2, operation="CREATE")
            q.mark_synced(synced.id)
            s.commit()

        svc = CleanupService(session_factory=db.session, data_dir=tmp_path)
        result = svc.run_once(at=T0)
        assert result["deleted_queue"] == 1

        with db.session() as s:
            from sqlalchemy import select
            rows = list(s.scalars(select(SyncQueueItem)))
            assert len(rows) == 1
            assert rows[0].id == pending.id
    finally:
        db.dispose()


def test_cleanup_respects_retention(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    try:
        # Create a synced screenshot file
        ws_id = 1
        with db.session() as s:
            u_repo = UserRepository(s)
            user = u_repo.upsert(external_user_id="ext-1", email="a@example.com", display_name="A", team_name="T")
            s.flush()
            sess_repo = SessionRepository(s)
            ws = sess_repo.create(user_id=user.id, started_at=T0)
            s.commit()
            ws_id = ws.id

        file_path = tmp_path / "pending" / "shot.jpg"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(b"jpeg-data")

        with db.session() as s:
            repo = ScreenshotRepository(s)
            shot = repo.add(session_id=ws_id, captured_at=T0, activity_state=ActivityState.ACTIVE, file_path=str(file_path))
            repo.mark_synced(shot.id, synced_at=T0)
            s.commit()

        # retention 7 days -> not yet deletable at +1 day
        svc = CleanupService(session_factory=db.session, data_dir=tmp_path, retention_days=7)
        result = svc.run_once(at=T0 + timedelta(days=1))
        assert result["deleted_files"] == 0
        assert file_path.exists()

        # At +8 days -> deletable
        result2 = svc.run_once(at=T0 + timedelta(days=8))
        assert result2["deleted_files"] == 1
        assert not file_path.exists()
    finally:
        db.dispose()


def test_cleanup_preserves_unsynced_screenshots(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    try:
        with db.session() as s:
            u_repo = UserRepository(s)
            user = u_repo.upsert(external_user_id="ext-1", email="a@example.com", display_name="A", team_name="T")
            s.flush()
            sess_repo = SessionRepository(s)
            ws = sess_repo.create(user_id=user.id, started_at=T0)
            s.commit()
            ws_id = ws.id

        file_path = tmp_path / "pending" / "keep.jpg"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(b"keep")

        with db.session() as s:
            repo = ScreenshotRepository(s)
            repo.add(session_id=ws_id, captured_at=T0, activity_state=ActivityState.ACTIVE, file_path=str(file_path))
            s.commit()

        svc = CleanupService(session_factory=db.session, data_dir=tmp_path, retention_days=0)
        result = svc.run_once(at=T0)
        assert result["deleted_files"] == 0
        assert file_path.exists()
    finally:
        db.dispose()


def test_cleanup_immediate_retention_zero(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    try:
        with db.session() as s:
            u_repo = UserRepository(s)
            user = u_repo.upsert(external_user_id="ext-1", email="a@example.com", display_name="A", team_name="T")
            s.flush()
            sess_repo = SessionRepository(s)
            ws = sess_repo.create(user_id=user.id, started_at=T0)
            s.commit()
            ws_id = ws.id

        file_path = tmp_path / "pending" / "imm.jpg"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(b"data")

        with db.session() as s:
            repo = ScreenshotRepository(s)
            shot = repo.add(session_id=ws_id, captured_at=T0, activity_state=ActivityState.ACTIVE, file_path=str(file_path))
            repo.mark_synced(shot.id, synced_at=T0)
            s.commit()

        svc = CleanupService(session_factory=db.session, data_dir=tmp_path, retention_days=0)
        result = svc.run_once(at=T0)
        assert result["deleted_files"] == 1
        assert not file_path.exists()
    finally:
        db.dispose()
