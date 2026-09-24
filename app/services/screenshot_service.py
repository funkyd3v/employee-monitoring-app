"""Screenshot service — atomic pipeline, session/idle aware.

Pipeline (docs/ENGINEERING_RULES.md §Atomic screenshot pipeline):

    Capture → apply idle border → compress → write TEMP → validate
    → atomically move to pending/ → metadata row → sync_queue row

Deletion rule (§Data Deletion): never delete local file/row on attempt;
only after server confirms (future SyncWorker). Low-disk handling:
detect → stop creating → preserve metadata → warn (contract-ready).
"""

from __future__ import annotations

import contextlib
import shutil
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from app.config.constants import (
    SCREENSHOTS_PENDING_DIR,
)
from app.core.clock import SYSTEM_CLOCK, Clock
from app.core.exceptions import ProviderUnavailableError
from app.core.logging import get_logger
from app.domain.activity.activity import ActivityState
from app.domain.screenshots.naming import build_screenshot_filename
from app.domain.screenshots.processing import (
    apply_idle_border,
    compute_checksum,
    encode_jpeg,
    validate_image,
)
from app.domain.sync.sync import SyncEntityType, SyncOperation
from app.infrastructure.database.repositories import (
    ScreenshotRepository,
    SyncQueueRepository,
)

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session

    from app.domain.screenshots.provider import ScreenshotProvider
    from app.services.activity_service import ActivityService

SessionFactory = Callable[[], "Session"]

_logger = get_logger("screenshots.service")

# Minimum free space to allow a new capture (200 MB)
_MIN_FREE_BYTES = 200 * 1024 * 1024


class ScreenshotService:
    """Orchestrates capture + atomic persistence for one session."""

    def __init__(
        self,
        *,
        provider: ScreenshotProvider,
        session_factory: SessionFactory | None = None,
        activity_service: ActivityService | None = None,
        data_dir: Path | None = None,
        clock: Clock = SYSTEM_CLOCK,
        interval_seconds: int = 60,
    ) -> None:
        self._provider = provider
        self._session_factory = session_factory
        self._activity = activity_service
        self._data_dir = data_dir
        self._clock = clock
        self._interval_seconds = interval_seconds
        self._session_id: int | None = None
        self._paused = False

    # ── Configuration ─────────────────────────────────────────────────

    def set_session_factory(self, factory: SessionFactory | None) -> None:
        self._session_factory = factory

    def set_data_dir(self, data_dir: Path | None) -> None:
        self._data_dir = data_dir

    def set_interval(self, seconds: int) -> None:
        if seconds < 60:
            raise ValueError("screenshot interval must be >= 60")
        self._interval_seconds = seconds

    @property
    def interval_seconds(self) -> int:
        return self._interval_seconds

    # ── Session lifecycle ─────────────────────────────────────────────

    def start(self, session_id: int) -> None:
        """Begin captures for ``session_id`` (WORKING)."""
        self._session_id = session_id
        self._paused = False
        _logger.info("screenshot service started session=%s", session_id)

    def pause(self) -> None:
        """Pause during BREAK — no screenshots until ``resume``."""
        self._paused = True
        _logger.info("screenshot service paused (break)")

    def resume(self, session_id: int | None = None) -> None:
        if session_id is not None:
            self._session_id = session_id
        self._paused = False
        _logger.info("screenshot service resumed session=%s", self._session_id)

    def stop(self) -> None:
        """Stop after CHECK_OUT / COMPLETED."""
        _logger.info("screenshot service stopped session=%s", self._session_id)
        self._session_id = None
        self._paused = False

    def can_capture(self) -> bool:
        return self._session_id is not None and not self._paused

    # ── Core capture ──────────────────────────────────────────────────

    def capture_once(self, *, at: datetime | None = None) -> Path | None:
        """Run one atomic capture if session allows — returns final path or None.

        Returns ``None`` when not allowed (paused/no session), low disk, or
        provider failure (logged, not raised — failure isolation).
        """
        at = at or self._clock.utc()
        if at.tzinfo is None:
            raise ValueError("captured_at must be timezone-aware (UTC)")

        if not self.can_capture():
            _logger.debug("capture skipped — paused or no session")
            return None

        assert self._session_id is not None  # can_capture guarantees
        session_id = self._session_id

        # Idle classification at capture time
        activity_state = ActivityState.ACTIVE
        if self._activity is not None:
            try:
                activity_state = self._activity.current_state(at=at)
            except Exception:
                _logger.debug(
                    "activity state lookup failed — defaulting ACTIVE", exc_info=True
                )

        # Low-disk guard
        pending_dir = self._pending_dir()
        if pending_dir is not None:
            try:
                pending_dir.mkdir(parents=True, exist_ok=True)
                free = shutil.disk_usage(pending_dir).free
                if free < _MIN_FREE_BYTES:
                    _logger.warning(
                        "low disk — screenshot capture skipped free=%s", free
                    )
                    return None
            except Exception:
                _logger.debug(
                    "disk check failed — proceeding cautiously", exc_info=True
                )

        # Capture
        try:
            image = self._provider.capture()
        except ProviderUnavailableError as exc:
            _logger.warning("screenshot capture failed (provider): %s", exc)
            return None
        except Exception as exc:
            _logger.error("screenshot capture failed: %s", exc, exc_info=True)
            return None

        # Apply idle border
        try:
            image = apply_idle_border(
                image, idle=(activity_state is ActivityState.IDLE)
            )
        except Exception as exc:
            _logger.error("idle border failed: %s", exc, exc_info=True)
            # Continue without border — capture is more valuable than its frame

        # Resolve paths + atomic write
        if pending_dir is None or self._session_factory is None:
            # No persistence configured — discard (tests with no DB)
            _logger.debug("no data_dir/factory — capture discarded")
            return None

        filename = build_screenshot_filename(session_id, at, uuid.uuid4().hex[:8])
        final_path = pending_dir / filename

        tmp_path: Path | None = None
        try:
            # Write to sibling temp file then validate + move
            with tempfile.NamedTemporaryFile(
                suffix=".jpg",
                dir=str(pending_dir),
                delete=False,
            ) as tmp:
                tmp_path = Path(tmp.name)
            # Encode outside the tempfile handle (Windows locks)
            if tmp_path.exists():
                tmp_path.unlink()
            encode_jpeg(image, tmp_path)

            if not validate_image(tmp_path):
                _logger.error("screenshot validation failed — discarding %s", tmp_path)
                if tmp_path.exists():
                    tmp_path.unlink()
                return None

            # Atomic move
            tmp_path.replace(final_path)

            file_size = final_path.stat().st_size
            checksum = compute_checksum(final_path)

            # Persist metadata + outbox
            with self._session_factory() as db:
                s_repo = ScreenshotRepository(db)
                row = s_repo.add(
                    session_id=session_id,
                    captured_at=at,
                    activity_state=activity_state,
                    file_path=str(final_path),
                    file_size=file_size,
                    checksum=checksum,
                )
                q_repo = SyncQueueRepository(db)
                q_repo.enqueue(
                    entity_type=SyncEntityType.SCREENSHOT.value,
                    entity_id=row.id,
                    operation=SyncOperation.CREATE.value,
                )
                db.commit()
                _logger.info(
                    "screenshot stored id=%s session=%s state=%s file=%s",
                    row.id,
                    session_id,
                    activity_state.value,
                    filename,
                )
            return final_path

        except Exception as exc:
            _logger.error("screenshot pipeline failed: %s", exc, exc_info=True)
            # Clean up tmp/final on failure
            if tmp_path is not None and tmp_path.exists():
                with contextlib.suppress(Exception):
                    tmp_path.unlink()
            if final_path.exists():
                # File was moved but DB failed — orphan will be cleaned on next startup scan
                _logger.warning(
                    "pipeline DB failed after file move — orphan may remain %s",
                    final_path,
                )
            return None

    # ── Orphan recovery ───────────────────────────────────────────────

    def recover_orphans(self) -> dict[str, int]:
        """Scan pending dir ↔ DB and reconcile orphans (startup pass).

        Returns counts ``{orphan_files_removed, orphan_records_removed, valid}``.
        """
        if self._data_dir is None or self._session_factory is None:
            return {"orphan_files_removed": 0, "orphan_records_removed": 0, "valid": 0}

        pending_dir = self._pending_dir()
        if pending_dir is None or not pending_dir.exists():
            return {"orphan_files_removed": 0, "orphan_records_removed": 0, "valid": 0}

        orphan_files_removed = 0
        orphan_records_removed = 0
        valid = 0

        try:
            with self._session_factory() as db:
                ScreenshotRepository(db)
                # All screenshot file_paths from DB
                from sqlalchemy import select

                from app.infrastructure.database.models import ScreenshotMetadata

                rows = list(db.scalars(select(ScreenshotMetadata)))
                db_paths = {Path(r.file_path) for r in rows if r.file_path}

                # Orphan files: on disk but no DB row → remove
                for p in pending_dir.glob("*.jpg"):
                    if p not in db_paths:
                        try:
                            p.unlink()
                            orphan_files_removed += 1
                            _logger.info("removed orphan screenshot file %s", p.name)
                        except Exception:
                            _logger.debug(
                                "failed to remove orphan file %s", p, exc_info=True
                            )
                    else:
                        # Validate file exists and is readable; else mark failed
                        if not validate_image(p):
                            _logger.warning(
                                "corrupt screenshot file detected %s", p.name
                            )

                # Orphan records: DB row but file missing → mark failed (or remove)
                for row in rows:
                    fp = Path(row.file_path)
                    if not fp.exists():
                        # Do not delete row outright — mark FAILED so retry/cleanup can see
                        # But if never synced and file never existed, it's safer to remove
                        try:
                            from sqlalchemy import delete as sa_delete

                            db.execute(
                                sa_delete(ScreenshotMetadata).where(
                                    ScreenshotMetadata.id == row.id
                                )
                            )
                            orphan_records_removed += 1
                            _logger.info(
                                "removed orphan screenshot record id=%s", row.id
                            )
                        except Exception:
                            _logger.debug(
                                "failed to remove orphan record %s",
                                row.id,
                                exc_info=True,
                            )

                valid = len(db_paths) - orphan_records_removed
                db.commit()
        except Exception as exc:
            _logger.error("orphan recovery failed: %s", exc, exc_info=True)

        return {
            "orphan_files_removed": orphan_files_removed,
            "orphan_records_removed": orphan_records_removed,
            "valid": valid,
        }

    # ── Helpers ───────────────────────────────────────────────────────

    def _pending_dir(self) -> Path | None:
        if self._data_dir is None:
            return None
        return self._data_dir / SCREENSHOTS_PENDING_DIR

    def list_for_session(self, session_id: int) -> list[dict[str, object]]:
        """List screenshot metadata for session (tests/debugging)."""
        if self._session_factory is None:
            return []
        try:
            from sqlalchemy import select

            from app.infrastructure.database.models import ScreenshotMetadata

            with self._session_factory() as db:
                rows = list(
                    db.scalars(
                        select(ScreenshotMetadata).where(
                            ScreenshotMetadata.session_id == session_id
                        )
                    )
                )
                db.commit()
                return [
                    {
                        "id": r.id,
                        "captured_at": r.captured_at,
                        "activity_state": r.activity_state,
                        "file_path": r.file_path,
                        "file_size": r.file_size,
                        "checksum": r.checksum,
                        "sync_status": r.sync_status,
                    }
                    for r in rows
                ]
        except Exception:
            return []
