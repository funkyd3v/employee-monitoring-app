"""Repository layer — the only code that touches SQLAlchemy/SQLite.

Rule (docs/DATA_MODEL.md § Access rules): all DB access goes through these
repositories, never raw SQL scattered in services or UI. Services depend on
a repository interface; the concrete wiring lives in ``container.py``.

Two patterns coexist here:
* **Read/query repos** (sessions, screenshots, sync_queue) return ORM rows;
  callers map to domain models.
* **Key-value state** (settings, app_state) exposes dict-like APIs.

Deletion discipline (docs/ENGINEERING_RULES.md §Data Deletion Rule): nothing
in this layer deletes screenshots or outbox rows implicitly — the sync
service deletes *only after* the server confirms persistence.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, func, select

from app.core.clock import utc_now
from app.domain.sessions.session import WorkSessionStatus
from app.domain.sync.sync import SyncStatus
from app.infrastructure.database.models import (
    ActivityPeriodRecord,
    AppStateRow,
    BreakRecord,
    ScreenshotMetadata,
    Setting,
    SyncQueueItem,
    User,
    WorkSession,
)

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session

    from app.domain.activity.activity import ActivityState


class UserRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def upsert(
        self,
        *,
        external_user_id: str | None,
        email: str,
        display_name: str | None,
        team_name: str | None,
    ) -> User:
        """Insert the user or update the existing row (matched by email)."""
        row = self._session.scalar(select(User).where(User.email == email))
        if row is None:
            row = User(
                external_user_id=external_user_id,
                email=email,
                display_name=display_name,
                team_name=team_name,
            )
            self._session.add(row)
        else:
            row.external_user_id = external_user_id
            row.display_name = display_name
            row.team_name = team_name
        self._session.flush()
        return row

    def get_by_email(self, email: str) -> User | None:
        return self._session.scalar(select(User).where(User.email == email))

    def get_by_id(self, user_id: int) -> User | None:
        return self._session.get(User, user_id)


class SessionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        *,
        user_id: int,
        started_at: datetime,
    ) -> WorkSession:
        row = WorkSession(
            user_id=user_id,
            started_at=started_at,
            status=WorkSessionStatus.WORKING.value,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def get_by_id(self, session_id: int) -> WorkSession | None:
        return self._session.get(WorkSession, session_id)

    def get_active(self, user_id: int) -> WorkSession | None:
        """The user's current unfinished session (WORKING or BREAK)."""
        return self._session.scalar(
            select(WorkSession)
            .where(WorkSession.user_id == user_id)
            .where(
                WorkSession.status.in_(
                    [WorkSessionStatus.WORKING.value, WorkSessionStatus.BREAK.value]
                )
            )
            .order_by(WorkSession.started_at.desc())
            .limit(1)
        )

    def update_status(self, session_id: int, status: WorkSessionStatus) -> None:
        row = self.get_by_id(session_id)
        if row is not None:
            row.status = status.value
            self._session.flush()

    def checkout(
        self,
        session_id: int,
        *,
        ended_at: datetime,
        total_work_seconds: int,
    ) -> WorkSession | None:
        row = self.get_by_id(session_id)
        if row is None:
            return None
        row.ended_at = ended_at
        row.total_work_seconds = total_work_seconds
        row.status = WorkSessionStatus.COMPLETED.value
        self._session.flush()
        return row

    def list_for_user(self, user_id: int, *, limit: int = 20) -> list[WorkSession]:
        return list(
            self._session.scalars(
                select(WorkSession)
                .where(WorkSession.user_id == user_id)
                .order_by(WorkSession.started_at.desc())
                .limit(limit)
            )
        )


class BreakRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def start(self, *, session_id: int, started_at: datetime) -> BreakRecord:
        row = BreakRecord(session_id=session_id, started_at=started_at)
        self._session.add(row)
        self._session.flush()
        return row

    def end_open(self, break_id: int, *, ended_at: datetime) -> BreakRecord | None:
        """Close a break, computing its duration from the persisted start."""
        row = self._session.get(BreakRecord, break_id)
        if row is None or row.ended_at is not None:
            return row
        row.ended_at = ended_at
        row.duration_seconds = max(0, int((ended_at - row.started_at).total_seconds()))
        self._session.flush()
        return row

    def open_for_session(self, session_id: int) -> BreakRecord | None:
        return self._session.scalar(
            select(BreakRecord)
            .where(BreakRecord.session_id == session_id)
            .where(BreakRecord.ended_at.is_(None))
            .limit(1)
        )

    def accumulate_seconds(self, session_id: int) -> int:
        total = self._session.scalar(
            select(func.coalesce(func.sum(BreakRecord.duration_seconds), 0)).where(
                BreakRecord.session_id == session_id
            )
        )
        return int(total or 0)

    def list_for_session(self, session_id: int) -> list[BreakRecord]:
        return list(
            self._session.scalars(
                select(BreakRecord)
                .where(BreakRecord.session_id == session_id)
                .order_by(BreakRecord.started_at.asc())
            )
        )


class ActivityRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def append(
        self,
        *,
        session_id: int,
        state: ActivityState,
        started_at: datetime,
        ended_at: datetime | None = None,
    ) -> ActivityPeriodRecord:
        row = ActivityPeriodRecord(
            session_id=session_id,
            state=state.value,
            started_at=started_at,
            ended_at=ended_at,
        )
        row.duration_seconds = (
            0
            if ended_at is None
            else max(0, int((ended_at - started_at).total_seconds()))
        )
        self._session.add(row)
        self._session.flush()
        return row

    def close_open(self, *, ended_at: datetime) -> list[ActivityPeriodRecord]:
        """Close any still-open periods across sessions (check-out/exit)."""
        open_rows = list(
            self._session.scalars(
                select(ActivityPeriodRecord).where(
                    ActivityPeriodRecord.ended_at.is_(None)
                )
            )
        )
        for row in open_rows:
            row.ended_at = ended_at
            row.duration_seconds = max(
                0, int((ended_at - row.started_at).total_seconds())
            )
        self._session.flush()
        return open_rows

    def list_for_session(self, session_id: int) -> list[ActivityPeriodRecord]:
        return list(
            self._session.scalars(
                select(ActivityPeriodRecord)
                .where(ActivityPeriodRecord.session_id == session_id)
                .order_by(ActivityPeriodRecord.started_at.asc())
            )
        )


class ScreenshotRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self,
        *,
        session_id: int,
        captured_at: datetime,
        activity_state: ActivityState,
        file_path: str,
        file_size: int | None = None,
        checksum: str | None = None,
    ) -> ScreenshotMetadata:
        row = ScreenshotMetadata(
            session_id=session_id,
            captured_at=captured_at,
            activity_state=activity_state.value,
            file_path=file_path,
            file_size=file_size,
            checksum=checksum,
            sync_status=SyncStatus.PENDING.value,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def get_by_id(self, screenshot_id: int) -> ScreenshotMetadata | None:
        return self._session.get(ScreenshotMetadata, screenshot_id)

    def pending_batch(self, *, limit: int) -> list[ScreenshotMetadata]:
        return list(
            self._session.scalars(
                select(ScreenshotMetadata)
                .where(ScreenshotMetadata.sync_status == SyncStatus.PENDING.value)
                .order_by(ScreenshotMetadata.created_at.asc())
                .limit(limit)
            )
        )

    def record_attempt(self, screenshot_id: int, *, at: datetime) -> None:
        row = self.get_by_id(screenshot_id)
        if row is not None:
            row.attempt_count += 1
            row.last_attempt_at = at
            self._session.flush()

    def mark_sync_started(self, screenshot_id: int) -> None:
        row = self.get_by_id(screenshot_id)
        if row is not None:
            row.sync_status = SyncStatus.SYNCING.value
            self._session.flush()

    def mark_failed(self, screenshot_id: int) -> None:
        row = self.get_by_id(screenshot_id)
        if row is not None:
            row.sync_status = SyncStatus.FAILED.value
            self._session.flush()

    def mark_synced(self, screenshot_id: int, *, synced_at: datetime) -> None:
        row = self.get_by_id(screenshot_id)
        if row is not None:
            row.sync_status = SyncStatus.SYNCED.value
            row.synced_at = synced_at
            self._session.flush()


class SyncQueueRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def enqueue(
        self,
        *,
        entity_type: str,
        entity_id: int,
        operation: str,
    ) -> SyncQueueItem:
        row = SyncQueueItem(
            entity_type=entity_type,
            entity_id=entity_id,
            operation=operation,
            status=SyncStatus.PENDING.value,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def pending_batch(self, *, limit: int) -> list[SyncQueueItem]:
        return list(
            self._session.scalars(
                select(SyncQueueItem)
                .where(
                    SyncQueueItem.status.in_(
                        [SyncStatus.PENDING.value, SyncStatus.FAILED.value]
                    )
                )
                .order_by(SyncQueueItem.created_at.asc())
                .limit(limit)
            )
        )

    def record_attempt(self, queue_id: int, *, at: datetime) -> None:
        row = self._session.get(SyncQueueItem, queue_id)
        if row is not None:
            row.attempt_count += 1
            row.last_attempt_at = at
            self._session.flush()

    def mark_sync_started(self, queue_id: int) -> None:
        row = self._session.get(SyncQueueItem, queue_id)
        if row is not None:
            row.status = SyncStatus.SYNCING.value
            self._session.flush()

    def record_error(self, queue_id: int, message: str) -> None:
        row = self._session.get(SyncQueueItem, queue_id)
        if row is not None:
            row.status = SyncStatus.FAILED.value
            row.last_error = message[:1024]
            self._session.flush()

    def mark_synced(self, queue_id: int) -> None:
        """Called by sync service only after server confirms persistence."""
        row = self._session.get(SyncQueueItem, queue_id)
        if row is not None:
            row.status = SyncStatus.SYNCED.value
            self._session.flush()

    def delete_after_confirmed(self, queue_id: int) -> None:
        """Delete an outbox row *only after* server confirms persistence."""
        self._session.execute(delete(SyncQueueItem).where(SyncQueueItem.id == queue_id))
        self._session.flush()


class SettingsRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, key: str) -> str | None:
        row = self._session.get(Setting, key)
        return row.value if row else None

    def set(self, key: str, value: str) -> None:
        row = self._session.get(Setting, key)
        if row is None:
            self._session.add(Setting(key=key, value=value))
        else:
            row.value = value
        self._session.flush()

    def get_all(self) -> dict[str, str]:
        return {r.key: r.value for r in self._session.scalars(select(Setting))}


class AppStateRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, key: str) -> str | None:
        row = self._session.get(AppStateRow, key)
        return row.value if row else None

    def set(self, key: str, value: str) -> None:
        row = self._session.get(AppStateRow, key)
        if row is None:
            self._session.add(AppStateRow(key=key, value=value, updated_at=utc_now()))
        else:
            row.value = value
            row.updated_at = utc_now()
        self._session.flush()

    def get_all(self) -> dict[str, str]:
        return {r.key: r.value for r in self._session.scalars(select(AppStateRow))}


def to_row_dict(model_object: Any) -> dict[str, Any]:
    """Serialize an ORM row to plain dict (no dates serialized)."""
    columns = model_object.__table__.columns
    return {c.name: getattr(model_object, c.name) for c in columns}
