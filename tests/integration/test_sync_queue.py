"""Integration tests for offline queue & recovery (phase 8 DOD)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from app.config.settings import AppSettings, LocalConfig, ServerPolicy
from app.core.container import Container
from app.domain.activity.activity import ActivityState
from app.domain.sync.provider import ConnectivityState
from app.domain.sync.sync import SyncStatus
from app.infrastructure.database.db import Database
from app.infrastructure.database.migrations import migrate
from app.infrastructure.database.models import SyncQueueItem
from app.infrastructure.database.repositories import (
    ScreenshotRepository,
    SyncQueueRepository,
)
from app.infrastructure.network.sync_adapter import DummySyncProvider
from app.services.sync_service import SyncService

if TYPE_CHECKING:
    from pathlib import Path

T0 = datetime(2026, 9, 23, 10, 0, tzinfo=UTC)


def _container(tmp_path: Path) -> Container:
    local = LocalConfig(data_dir=tmp_path / "data", log_level="DEBUG")
    server = ServerPolicy(sync_batch_limit=20, retention_days=0)
    settings = AppSettings(local=local, server=server)
    container = Container(settings)
    container.open_database()
    return container


def test_offline_queue_preserved_and_synced_after_online(tmp_path: Path) -> None:
    container = _container(tmp_path)
    try:
        # Use offline provider initially
        container.sync_provider.set_connectivity(ConnectivityState.OFFLINE)  # type: ignore[attr-defined]

        # Check-in creates work_session + queue item
        container.auth_service.login("employee@example.com", "secret")  # dummy
        container.session_service.set_user(container.auth_service.current_user_id())
        container.session_service.check_in()

        # Verify queue has entry while offline
        svc = container.sync_service
        # Sync while offline should not delete
        result = svc.sync_once(at=T0)
        assert result["skipped_offline"] == 1

        with container.database.session() as s:
            from sqlalchemy import select

            rows = list(s.scalars(select(SyncQueueItem)))
            assert len(rows) >= 1

        # Go online and sync
        container.sync_provider.set_connectivity(ConnectivityState.ONLINE)  # type: ignore[attr-defined]
        result2 = svc.sync_once(at=T0 + timedelta(seconds=5))
        assert result2["synced"] >= 1

        # Queue should be cleared (deleted after confirmed)
        with container.database.session() as s:
            rows = list(s.scalars(select(SyncQueueItem)))
            # May still have screenshots pending but work_session queue should be gone
            # Filter work_session
            ws_rows = [r for r in rows if r.entity_type == "work_session"]
            assert len(ws_rows) == 0
    finally:
        container.close_database()


def test_screenshot_offline_queue_and_cleanup(tmp_path: Path) -> None:
    container = _container(tmp_path)
    try:
        container.auth_service.login("employee@example.com", "secret")
        container.session_service.set_user(container.auth_service.current_user_id())
        container.session_service.check_in()
        session_id = container.session_service.machine.session.id  # type: ignore[union-attr]
        assert session_id is not None

        # Force offline
        container.sync_provider.set_connectivity(ConnectivityState.OFFLINE)  # type: ignore[attr-defined]

        # Capture screenshot via service (needs pending dir)
        # Create a dummy file via provider is DummyScreenshotProvider — but capture_once needs real capture
        # Instead manually insert screenshot row to simulate capture
        from app.infrastructure.database.repositories import ScreenshotRepository

        file_path = (
            container.settings.data_dir / "screenshots" / "pending" / "test_shot.jpg"
        )
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(b"fake-image-data" * 100)

        with container.database.session() as s:
            repo = ScreenshotRepository(s)
            shot = repo.add(
                session_id=session_id,
                captured_at=T0,
                activity_state=ActivityState.ACTIVE,
                file_path=str(file_path),
                file_size=file_path.stat().st_size,
                checksum="abc",
            )
            from app.domain.sync.sync import SyncEntityType, SyncOperation
            from app.infrastructure.database.repositories import SyncQueueRepository

            q_repo = SyncQueueRepository(s)
            q_repo.enqueue(
                entity_type=SyncEntityType.SCREENSHOT.value,
                entity_id=shot.id,
                operation=SyncOperation.CREATE.value,
            )
            s.commit()

        # Sync offline -> no delete, file preserved
        svc = container.sync_service
        result = svc.sync_once(at=T0)
        assert result["skipped_offline"] == 1
        assert file_path.exists()

        # Go online -> file uploaded, marked synced but not yet deleted (cleanup will)
        container.sync_provider.set_connectivity(ConnectivityState.ONLINE)  # type: ignore[attr-defined]
        result2 = svc.sync_once(at=T0 + timedelta(seconds=5))
        assert result2["synced"] >= 1

        # Cleanup with retention 0 should delete file
        cleanup = container.cleanup_service
        cleanup.run_once(at=T0 + timedelta(seconds=6))
        assert not file_path.exists()
    finally:
        container.close_database()


def test_recovery_of_stale_syncing(tmp_path: Path) -> None:
    db = Database(tmp_path / "rec.db")
    migrate(db.engine)
    try:
        # Create queue item and mark SYNCING (crash left)
        with db.session() as s:
            q = SyncQueueRepository(s)
            item = q.enqueue(
                entity_type="work_session", entity_id=1, operation="CREATE"
            )
            q.mark_sync_started(item.id)
            s.commit()

        provider = DummySyncProvider()
        svc = SyncService(provider=provider, session_factory=db.session)
        # Before recovery, sync would see no pending (only SYNCING)
        with db.session() as s:
            assert len(SyncQueueRepository(s).pending_batch(limit=10)) == 0
            assert (
                len(
                    list(
                        s.scalars(
                            __import__("sqlalchemy")
                            .select(SyncQueueItem)
                            .where(SyncQueueItem.status == SyncStatus.SYNCING.value)
                        )
                    )
                )
                == 1
            )

        svc.recover_stale()
        # After recovery, pendingBatch should find it
        with db.session() as s:
            assert len(SyncQueueRepository(s).pending_batch(limit=10)) == 1
    finally:
        db.dispose()


def test_orphan_file_record_recovery(tmp_path: Path) -> None:
    container = _container(tmp_path)
    try:
        container.auth_service.login("employee@example.com", "secret")
        container.session_service.set_user(container.auth_service.current_user_id())
        container.session_service.check_in()
        session_id = container.session_service.machine.session.id  # type: ignore[union-attr]

        # Orphan file: on disk but no DB row
        orphan = container.settings.data_dir / "screenshots" / "pending" / "orphan.jpg"
        orphan.parent.mkdir(parents=True, exist_ok=True)
        orphan.write_bytes(b"orphan-data")

        # Orphan record: DB row but file missing
        missing_path = (
            container.settings.data_dir / "screenshots" / "pending" / "missing.jpg"
        )
        with container.database.session() as s:
            repo = ScreenshotRepository(s)
            repo.add(
                session_id=session_id,
                captured_at=T0,
                activity_state=ActivityState.ACTIVE,
                file_path=str(missing_path),
            )
            s.commit()

        # Run screenshot orphan recovery
        result = container.screenshot_service.recover_orphans()
        assert result["orphan_files_removed"] == 1
        assert not orphan.exists()

        # Orphan queue for non-existent entity
        with container.database.session() as s:
            q = SyncQueueRepository(s)
            q.enqueue(entity_type="work_session", entity_id=99999, operation="CREATE")
            s.commit()

        orphans = container.sync_service.recover_orphans()
        assert orphans["orphan_queue_removed"] == 1
    finally:
        container.close_database()
