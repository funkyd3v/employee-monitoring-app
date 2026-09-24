"""Sync service — queue drain with confirm-then-delete and retry backoff.

Data deletion rule (docs/ENGINEERING_RULES.md §Data Deletion Rule): local
data is deleted only after the server confirms persistence. This service is
the *only* place that deletes synced outbox rows/files, gated by
:class:`SyncResult.success`.

Retry strategy (docs §Retry strategy): exponential backoff via
``backoff_for_attempt`` — immediate, 30s, 2m, 5m, 15m cap. Backoff is checked
before each per-item attempt; not-yet-due items are skipped.

Connectivity model (§Connectivity model): distinct states tracked; OFFLINE /
BACKEND_UNAVAILABLE short-circuit the drain without marking failures as
non-retryable.

Recovery: stale SYNCING rows (crash-left) are reset to PENDING on startup
via :meth:`recover_stale`.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from datetime import datetime  # noqa: TC003
from typing import TYPE_CHECKING, Any

from app.core.clock import SYSTEM_CLOCK, Clock
from app.core.logging import get_logger
from app.domain.sync.payloads import (
    build_activity_period_payload,
    build_break_payload,
    build_screenshot_payload,
    build_user_payload,
    build_work_session_payload,
)
from app.domain.sync.provider import ConnectivityState, SyncProvider, SyncResult
from app.domain.sync.sync import SyncStatus, backoff_for_attempt
from app.infrastructure.database.models import (
    ActivityPeriodRecord,
    BreakRecord,
    ScreenshotMetadata,
    SyncQueueItem,
    User,
    WorkSession,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

SessionFactory = Callable[[], "Session"]

_logger = get_logger("sync.service")


class SyncService:
    """Drains ``sync_queue`` + pending screenshots through a :class:`SyncProvider`."""

    def __init__(
        self,
        *,
        provider: SyncProvider,
        session_factory: SessionFactory | None = None,
        clock: Clock = SYSTEM_CLOCK,
        batch_limit: int = 20,
    ) -> None:
        self._provider = provider
        self._session_factory = session_factory
        self._clock = clock
        self._batch_limit = batch_limit
        self._connectivity = ConnectivityState.ONLINE

    @property
    def connectivity(self) -> ConnectivityState:
        return self._connectivity

    def set_session_factory(self, factory: SessionFactory | None) -> None:
        self._session_factory = factory

    # ── Startup recovery ────────────────────────────────────────────────

    def recover_stale(self) -> dict[str, int]:
        """Reset SYNCING rows left by a crash (persisted state recovery)."""
        if self._session_factory is None:
            return {"queue_reset": 0, "screenshots_reset": 0}
        try:
            with self._session_factory() as db:
                from app.infrastructure.database.repositories import (
                    ScreenshotRepository,
                    SyncQueueRepository,
                )

                q_repo = SyncQueueRepository(db)
                s_repo = ScreenshotRepository(db)
                queue_reset = q_repo.reset_stale_syncing()
                shots_reset = s_repo.reset_stale_syncing()
                db.commit()
                if queue_reset or shots_reset:
                    _logger.info(
                        "sync stale reset queue=%s screenshots=%s",
                        queue_reset,
                        shots_reset,
                    )
                return {"queue_reset": queue_reset, "screenshots_reset": shots_reset}
        except Exception as exc:
            _logger.error("recover_stale failed: %s", exc, exc_info=True)
            return {"queue_reset": 0, "screenshots_reset": 0}

    def recover_orphans(self) -> dict[str, int]:
        """Remove sync_queue rows whose entity no longer exists (orphan records).

        Handles crash where entity was cascade-deleted but queue row survived.
        Preserves screenshots sync_status orphans via ScreenshotService.recover_orphans.
        """
        if self._session_factory is None:
            return {"orphan_queue_removed": 0}
        removed = 0
        try:
            with self._session_factory() as db:
                from sqlalchemy import select

                from app.infrastructure.database.repositories import SyncQueueRepository

                q_repo = SyncQueueRepository(db)
                items = list(db.scalars(select(SyncQueueItem)))
                for item in items:
                    exists = True
                    try:
                        if item.entity_type == "work_session":
                            exists = db.get(WorkSession, item.entity_id) is not None
                        elif item.entity_type == "break":
                            exists = db.get(BreakRecord, item.entity_id) is not None
                        elif item.entity_type == "activity_period":
                            exists = db.get(ActivityPeriodRecord, item.entity_id) is not None
                        elif item.entity_type == "user":
                            exists = db.get(User, item.entity_id) is not None
                        elif item.entity_type == "screenshot":
                            exists = db.get(ScreenshotMetadata, item.entity_id) is not None
                        else:
                            # Unknown type — keep it for now, don't delete blindly
                            exists = True
                    except Exception:
                        exists = True
                    if not exists:
                        db.delete(item)
                        removed += 1
                        _logger.info(
                            "removed orphan queue item id=%s type=%s entity=%s",
                            item.id,
                            item.entity_type,
                            item.entity_id,
                        )
                if removed:
                    _logger.info("orphan queue cleanup removed=%s", removed)
                db.commit()
                # Also delegate screenshot orphan pass if data_dir available via provider?
                # ScreenshotService handles file↔DB orphans separately.
        except Exception as exc:
            _logger.error("recover_orphans failed: %s", exc, exc_info=True)
        return {"orphan_queue_removed": removed}

    # ── Single drain ───────────────────────────────────────────────────

    def sync_once(self, *, at: datetime | None = None) -> dict[str, int]:
        """Run one drain cycle — returns counts for observability.

        Returns ``{synced, failed, skipped_backoff, skipped_offline, pending}``.
        Never deletes data on failure; only on :attr:`SyncResult.success`.
        """
        at = at or self._clock.utc()
        self._ensure_tz(at)

        if self._session_factory is None:
            return {
                "synced": 0,
                "failed": 0,
                "skipped_backoff": 0,
                "skipped_offline": 0,
                "pending": 0,
            }

        # Check real backend reachability (not just network interface)
        try:
            state = self._provider.check_connectivity()
            self._connectivity = state
        except Exception as exc:
            _logger.debug("connectivity check failed: %s", exc, exc_info=True)
            state = ConnectivityState.BACKEND_UNAVAILABLE
            self._connectivity = state

        if state in (
            ConnectivityState.OFFLINE,
            ConnectivityState.BACKEND_UNAVAILABLE,
        ):
            _logger.debug("sync skipped — connectivity=%s", state)
            return {
                "synced": 0,
                "failed": 0,
                "skipped_backoff": 0,
                "skipped_offline": 1,
                "pending": self._pending_count(),
            }
        if state == ConnectivityState.AUTHENTICATION_REQUIRED:
            _logger.warning("sync skipped — auth required")
            return {
                "synced": 0,
                "failed": 0,
                "skipped_backoff": 0,
                "skipped_offline": 1,
                "pending": self._pending_count(),
            }

        self._connectivity = ConnectivityState.SYNCING
        synced = 0
        failed = 0
        skipped_backoff = 0

        # 1) Drain sync_queue items
        queue_stats = self._drain_queue(at)
        synced += queue_stats["synced"]
        failed += queue_stats["failed"]
        skipped_backoff += queue_stats["skipped_backoff"]

        # 2) Drain pending screenshots (those not via sync_queue or PENDING+FAILED)
        shot_stats = self._drain_screenshots(at)
        synced += shot_stats["synced"]
        failed += shot_stats["failed"]
        skipped_backoff += shot_stats["skipped_backoff"]

        # Final connectivity: online if we synced anything, else stay SYNCING or ONLINE
        self._connectivity = ConnectivityState.ONLINE

        if synced or failed or skipped_backoff:
            _logger.info(
                "sync_once synced=%s failed=%s skipped_backoff=%s at=%s",
                synced,
                failed,
                skipped_backoff,
                at,
            )

        return {
            "synced": synced,
            "failed": failed,
            "skipped_backoff": skipped_backoff,
            "skipped_offline": 0,
            "pending": self._pending_count(),
        }

    # ── Queue drain ─────────────────────────────────────────────────────

    def _drain_queue(self, at: datetime) -> dict[str, int]:
        if self._session_factory is None:
            return {"synced": 0, "failed": 0, "skipped_backoff": 0}
        synced = 0
        failed = 0
        skipped_backoff = 0

        try:
            with self._session_factory() as db:

                from app.infrastructure.database.repositories import (
                    SyncQueueRepository,
                )

                q_repo = SyncQueueRepository(db)
                items = q_repo.pending_batch(limit=self._batch_limit)
                # Need to decide backoff per item before marking SYNCING
                for item in items:
                    if not self._is_due(item, at):
                        skipped_backoff += 1
                        continue

                    payload = self._build_payload_for_item(db, item)
                    # Mark attempt + SYNCING inside same factory but new transaction?
                    # We already have db open — reuse it for state changes
                    q_repo.record_attempt(item.id, at=at)
                    q_repo.mark_sync_started(item.id)
                    db.commit()

                    # Call provider outside of held lock where possible — but we already
                    # committed, so provider call is outside DB transaction
                    try:
                        result: SyncResult = self._provider.sync_item(
                            entity_type=item.entity_type,
                            entity_id=item.entity_id,
                            operation=item.operation,
                            payload=payload,
                        )
                    except Exception as exc:
                        result = SyncResult.failed(
                            f"provider raised: {exc}",
                            retryable=True,
                            connectivity=ConnectivityState.BACKEND_UNAVAILABLE,
                        )

                    # Reopen session to persist outcome
                    with self._session_factory() as db2:
                        q_repo2 = SyncQueueRepository(db2)
                        if result.success:
                            q_repo2.mark_synced(item.id)
                            # Delete only after confirmed persistence
                            q_repo2.delete_after_confirmed(item.id)
                            db2.commit()
                            synced += 1
                            _logger.info(
                                "queue item synced id=%s type=%s",
                                item.id,
                                item.entity_type,
                            )
                        else:
                            # Failure — keep data, record error, respect retryable
                            if result.retryable:
                                q_repo2.record_error(item.id, result.error or "sync failed")  # noqa: E501
                            else:
                                q_repo2.record_error(
                                    item.id, result.error or "sync failed (non-retryable)"  # noqa: E501
                                )
                            db2.commit()
                            failed += 1
                            _logger.warning(
                                "queue item failed id=%s err=%s retryable=%s",
                                item.id,
                                result.error,
                                result.retryable,
                            )
                            # If auth required or backend unavailable,
                            if result.connectivity == ConnectivityState.AUTHENTICATION_REQUIRED:  # noqa: E501
                                self._connectivity = result.connectivity
                                break
                            if result.connectivity in (
                                ConnectivityState.OFFLINE,
                                ConnectivityState.BACKEND_UNAVAILABLE,
                            ) and failed >= 1:
                                # Short-circuit remaining items — backend is down
                                skipped_backoff += len(items) - (synced + failed + skipped_backoff)  # noqa: E501
                                break

                # Ensure outer session committed (already per item)
                db.commit()
        except Exception as exc:
            _logger.error("queue drain failed: %s", exc, exc_info=True)

        return {"synced": synced, "failed": failed, "skipped_backoff": skipped_backoff}

    # ── Screenshot drain ────────────────────────────────────────────────

    def _drain_screenshots(self, at: datetime) -> dict[str, int]:
        if self._session_factory is None:
            return {"synced": 0, "failed": 0, "skipped_backoff": 0}
        synced = 0
        failed = 0
        skipped_backoff = 0

        try:
            with self._session_factory() as db:
                from app.infrastructure.database.repositories import (
                    ScreenshotRepository,
                )

                s_repo = ScreenshotRepository(db)
                shots = s_repo.pending_with_failed_batch(limit=self._batch_limit)
                for shot in shots:
                    if not self._is_due_screenshot(shot, at):
                        skipped_backoff += 1
                        continue

                    metadata = build_screenshot_payload(
                        id=shot.id,
                        session_id=shot.session_id,
                        captured_at=shot.captured_at,
                        activity_state=shot.activity_state,
                        file_size=shot.file_size,
                        checksum=shot.checksum,
                        sync_status=shot.sync_status,
                    )

                    s_repo.record_attempt(shot.id, at=at)
                    s_repo.mark_sync_started(shot.id)
                    db.commit()

                    try:
                        result = self._provider.upload_screenshot(
                            screenshot_id=shot.id,
                            file_path=shot.file_path,
                            metadata=metadata,
                        )
                    except Exception as exc:
                        result = SyncResult.failed(
                            f"provider raised: {exc}",
                            retryable=True,
                            connectivity=ConnectivityState.BACKEND_UNAVAILABLE,
                        )

                    with self._session_factory() as db2:
                        s_repo2 = ScreenshotRepository(db2)
                        if result.success:
                            s_repo2.mark_synced(shot.id, synced_at=at)
                            db2.commit()
                            synced += 1
                            _logger.info("screenshot synced id=%s", shot.id)
                        else:
                            if result.retryable:
                                s_repo2.mark_failed(shot.id)
                            else:
                                # Missing file or empty — non-retryable → keep as FAILED
                                # but do not retry endlessly; mark FAILED
                                s_repo2.mark_failed(shot.id)
                            # Record attempt already done; ensure error visible via logs
                            db2.commit()
                            failed += 1
                            _logger.warning(
                                "screenshot failed id=%s err=%s retryable=%s",
                                shot.id,
                                result.error,
                                result.retryable,
                            )
                            if result.connectivity == ConnectivityState.AUTHENTICATION_REQUIRED:  # noqa: E501
                                self._connectivity = result.connectivity
                                break

                db.commit()
        except Exception as exc:
            _logger.error("screenshot drain failed: %s", exc, exc_info=True)

        return {"synced": synced, "failed": failed, "skipped_backoff": skipped_backoff}

    # ── Helpers ─────────────────────────────────────────────────────────

    def _is_due(self, item: SyncQueueItem, at: datetime) -> bool:
        """Backoff check: skip if ``now < last_attempt + backoff(attempt)``."""
        if item.last_attempt_at is None:
            return True
        next_attempt = item.attempt_count + 1
        delay = backoff_for_attempt(next_attempt)
        if delay == 0:
            return True
        try:
            last = item.last_attempt_at
            if last is not None:
                result: bool = at >= (last + _seconds_delay(delay))
                return result
            return True
        except Exception:
            return True

    def _is_due_screenshot(self, shot: ScreenshotMetadata, at: datetime) -> bool:
        if shot.last_attempt_at is None:
            return True
        next_attempt = shot.attempt_count + 1
        delay = backoff_for_attempt(next_attempt)
        if delay == 0:
            return True
        try:
            with contextlib.suppress(Exception):
                last = shot.last_attempt_at
                if last is not None:
                    result: bool = at >= (last + _seconds_delay(delay))
                    return result
        except Exception:
            return True
        return True

    def _build_payload_for_item(
        self, db: Session, item: SyncQueueItem
    ) -> dict[str, Any]:
        """Resolve entity row and build its JSON payload (or empty if missing)."""
        try:
            if item.entity_type == "work_session":
                ws = db.get(WorkSession, item.entity_id)
                if ws is not None:
                    return build_work_session_payload(
                        id=ws.id,
                        user_id=ws.user_id,
                        started_at=ws.started_at,
                        ended_at=ws.ended_at,
                        status=ws.status,
                        total_work_seconds=ws.total_work_seconds,
                    )
            elif item.entity_type == "break":
                br = db.get(BreakRecord, item.entity_id)
                if br is not None:
                    return build_break_payload(
                        id=br.id,
                        session_id=br.session_id,
                        started_at=br.started_at,
                        ended_at=br.ended_at,
                        duration_seconds=br.duration_seconds,
                    )
            elif item.entity_type == "activity_period":
                ap = db.get(ActivityPeriodRecord, item.entity_id)
                if ap is not None:
                    return build_activity_period_payload(
                        id=ap.id,
                        session_id=ap.session_id,
                        started_at=ap.started_at,
                        ended_at=ap.ended_at,
                        state=ap.state,
                        duration_seconds=ap.duration_seconds,
                    )
            elif item.entity_type == "user":
                u = db.get(User, item.entity_id)
                if u is not None:
                    return build_user_payload(
                        external_user_id=u.external_user_id,
                        email=u.email,
                        display_name=u.display_name,
                        team_name=u.team_name,
                    )
            elif item.entity_type == "screenshot":
                sm = db.get(ScreenshotMetadata, item.entity_id)
                if sm is not None:
                    return build_screenshot_payload(
                        id=sm.id,
                        session_id=sm.session_id,
                        captured_at=sm.captured_at,
                        activity_state=sm.activity_state,
                        file_size=sm.file_size,
                        checksum=sm.checksum,
                        sync_status=sm.sync_status,
                    )
        except Exception as exc:
            _logger.debug(
                "payload build failed for %s:%s: %s", item.entity_type, item.entity_id, exc, exc_info=True  # noqa: E501
            )
        return {
            "entity_type": item.entity_type,
            "entity_id": item.entity_id,
            "operation": item.operation,
        }

    def _pending_count(self) -> int:
        if self._session_factory is None:
            return 0
        try:
            with self._session_factory() as db:
                from sqlalchemy import func, select

                q_pending = db.scalar(
                    select(func.count())
                    .select_from(SyncQueueItem)
                    .where(
                        SyncQueueItem.status.in_([SyncStatus.PENDING.value, SyncStatus.FAILED.value])  # noqa: E501
                    )
                )
                s_pending = db.scalar(
                    select(func.count())
                    .select_from(ScreenshotMetadata)
                    .where(
                        ScreenshotMetadata.sync_status.in_([SyncStatus.PENDING.value, SyncStatus.FAILED.value])  # noqa: E501
                    )
                )
                return int(q_pending or 0) + int(s_pending or 0)
        except Exception:
            return 0

    @staticmethod
    def _ensure_tz(at: datetime) -> None:
        if at.tzinfo is None:
            raise ValueError("sync timestamps must be timezone-aware (UTC)")


def _seconds_delay(seconds: int):  # type: ignore[no-untyped-def]
    from datetime import timedelta

    return timedelta(seconds=seconds)


__all__ = ["SyncService"]
