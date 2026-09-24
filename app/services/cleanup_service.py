"""Cleanup service — deletion after confirmed sync + retention/low-disk.

Data deletion rule (docs/ENGINEERING_RULES.md §Data Deletion Rule): data is
deleted only after the server confirms persistence (checked via
``synced_at`` / ``SyncStatus.SYNCED``). This is the *only* service that
deletes local screenshot files or synced outbox rows.

Retention (docs/ARCHITECTURE.md §Performance): logs/screenshots/queue sizes
are monitored and capped. When low disk is detected, creation is stopped
(screenshot_service guard) and cleanup preserves metadata while freeing files.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from app.core.clock import SYSTEM_CLOCK, Clock
from app.core.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

SessionFactory = Callable[[], "Session"]

_logger = get_logger("cleanup.service")

# Keep synced files for this long before deleting local copy (even after sync).
DEFAULT_RETENTION_DAYS = 7
# Minimum free space to consider disk unhealthy (mirrors screenshot_service)
_MIN_FREE_BYTES = 200 * 1024 * 1024


class CleanupService:
    """Deletes local data only after sync is confirmed."""

    def __init__(
        self,
        *,
        session_factory: SessionFactory | None = None,
        data_dir: Path | None = None,
        clock: Clock = SYSTEM_CLOCK,
        retention_days: int = DEFAULT_RETENTION_DAYS,
    ) -> None:
        if retention_days < 0:
            raise ValueError("retention_days must be >= 0")
        self._session_factory = session_factory
        self._data_dir = data_dir
        self._clock = clock
        self._retention_days = retention_days

    def set_session_factory(self, factory: SessionFactory | None) -> None:
        self._session_factory = factory

    def set_data_dir(self, data_dir: Path | None) -> None:
        self._data_dir = data_dir

    @property
    def retention_days(self) -> int:
        return self._retention_days

    # ── Main entry ────────────────────────────────────────────────────

    def run_once(self, *, at: datetime | None = None) -> dict[str, int]:
        """Cleanup synced data — returns counts for observability."""
        at = at or self._clock.utc()
        self._ensure_tz(at)

        if self._session_factory is None:
            return {"deleted_files": 0, "deleted_queue": 0, "deleted_records": 0}

        deleted_files = 0
        deleted_queue = 0
        deleted_records = 0

        # 1) Delete synced queue rows (outbox)
        try:
            with self._session_factory() as db:
                from app.infrastructure.database.repositories import SyncQueueRepository

                repo = SyncQueueRepository(db)
                synced = repo.synced_batch(limit=200)
                for item in synced:
                    # Only delete if older than retention? For queue, delete immediately
                    repo.delete_after_confirmed(item.id)
                    deleted_queue += 1
                if deleted_queue:
                    _logger.info("cleanup deleted queue rows=%s", deleted_queue)
                db.commit()
        except Exception as exc:
            _logger.error("cleanup queue failed: %s", exc, exc_info=True)

        # 2) Delete synced screenshot files (keep DB row or delete per policy)
        # Policy: delete file if SYNCED and synced_at older than retention
        # retention_days=0 → delete immediately after sync.
        try:
            with self._session_factory() as db:
                from app.infrastructure.database.repositories import (
                    ScreenshotRepository,
                )

                s_repo = ScreenshotRepository(db)
                synced_shots = s_repo.synced_batch(limit=200)
                for shot in synced_shots:
                    if shot.synced_at is None:
                        continue
                    age = at - shot.synced_at
                    if age < timedelta(days=self._retention_days):
                        continue

                    file_path = Path(shot.file_path)
                    # Delete file only if it exists — never fail for missing
                    try:
                        if file_path.exists() and file_path.is_file():
                            file_path.unlink()
                            deleted_files += 1
                            _logger.info("cleanup deleted file %s", file_path.name)
                        # Optionally delete DB record after file deleted — keep
                        # to preserve audit trail. Uncomment to also purge:
                        # s_repo.delete(shot.id)
                        # deleted_records += 1
                    except Exception as exc:
                        _logger.warning(
                            "cleanup failed to delete %s: %s", file_path, exc
                        )

                db.commit()
        except Exception as exc:
            _logger.error("cleanup screenshots failed: %s", exc, exc_info=True)

        # 3) Check low disk and log warning (does not delete beyond synced)
        try:
            if self._data_dir is not None:
                pending_dir = self._data_dir / "screenshots" / "pending"
                pending_dir.mkdir(parents=True, exist_ok=True)
                free = shutil.disk_usage(pending_dir).free
                if free < _MIN_FREE_BYTES:
                    _logger.warning(
                        "low disk free=%s — preserved unsynced", free
                    )
        except Exception:
            _logger.debug("disk check failed during cleanup", exc_info=True)

        if deleted_files or deleted_queue or deleted_records:
            _logger.info(
                "cleanup done files=%s queue=%s records=%s at=%s",
                deleted_files,
                deleted_queue,
                deleted_records,
                at,
            )

        return {
            "deleted_files": deleted_files,
            "deleted_queue": deleted_queue,
            "deleted_records": deleted_records,
        }

    def low_disk(self) -> bool:
        """Return True when free space is below threshold."""
        if self._data_dir is None:
            return False
        try:
            pending_dir = self._data_dir / "screenshots" / "pending"
            pending_dir.mkdir(parents=True, exist_ok=True)
            free = shutil.disk_usage(pending_dir).free
            return free < _MIN_FREE_BYTES
        except Exception:
            return False

    @staticmethod
    def _ensure_tz(at: datetime) -> None:
        if at.tzinfo is None:
            raise ValueError("cleanup timestamps must be timezone-aware (UTC)")
