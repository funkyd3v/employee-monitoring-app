"""SQLAlchemy ORM models — one class per table in docs/DATA_MODEL.md.

Conventions:
* Persisted timestamps are UTC, timezone-aware datetimes
  (docs/ENGINEERING_RULES.md §Time & clock handling). :class:`UTCDateTime`
  stores them as ISO-8601 UTC text so values are readable in a sqlite shell
  and remain comparable.
* Foreign keys are wired but relationship() is deliberately kept minimal —
  repositories compose queries explicitly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator

from app.core.clock import utc_now
from app.domain.activity.activity import ActivityState
from app.domain.sessions.session import WorkSessionStatus
from app.domain.sync.sync import SyncStatus

if TYPE_CHECKING:
    from sqlalchemy.engine.interfaces import Dialect
    from sqlalchemy.sql.type_api import TypeEngine


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC datetime stored as ISO-8601 text.

    SQLAlchemy's plain ``DateTime`` drops tzinfo on SQLite and its SQLite
    dialect registers its own dialect-level bind processor, so a ``DateTime``
    subclass's ``bind_processor`` is never invoked. :class:`TypeDecorator`
    guarantees our processors run on *every* dialect. Values are stored as
    ``2026-09-23T10:30:00+00:00`` — readable in a sqlite shell and
    lexicographically sortable. Naive datetimes are a hard error at the write
    boundary.
    """

    impl = String
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[str]:
        return dialect.type_descriptor(String(64))

    def process_bind_param(
        self,
        value: datetime | None,
        dialect: Dialect,  # noqa: ARG002
    ) -> str | None:
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise ValueError(
                f"UTCDateTime expects datetime, got {type(value).__name__}"
            )
        if value.tzinfo is None:
            raise ValueError(
                "naive datetime refused; supply timezone-aware (UTC) value"
            )
        return value.astimezone(UTC).isoformat()

    def process_result_value(
        self,
        value: str | None,
        dialect: Dialect,  # noqa: ARG002
    ) -> datetime | None:
        if value is None:
            return None
        return datetime.fromisoformat(value)


class User(Base):
    """Local cache of the authenticated user (no password ever stored)."""

    __tablename__ = "users"
    __table_args__ = (Index("ix_users_email", "email", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    external_user_id: Mapped[str | None] = mapped_column(String(128))
    email: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(255))
    team_name: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, onupdate=utc_now
    )

    work_sessions: Mapped[list[WorkSession]] = relationship(back_populates="user")


class WorkSession(Base):
    """One row per Check-In → Check-Out cycle (docs/DATA_MODEL.md)."""

    __tablename__ = "work_sessions"
    __table_args__ = (
        Index("ix_work_sessions_user_status", "user_id", "status"),
        Index("ix_work_sessions_started_at", "started_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(
        String(16), default=WorkSessionStatus.WORKING.value
    )
    total_work_seconds: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, onupdate=utc_now
    )

    user: Mapped[User] = relationship(back_populates="work_sessions")
    breaks: Mapped[list[BreakRecord]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    activity_periods: Mapped[list[ActivityPeriodRecord]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    screenshots: Mapped[list[ScreenshotMetadata]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class BreakRecord(Base):
    """Break intervals within a session (docs/DATA_MODEL.md ``breaks``)."""

    __tablename__ = "breaks"
    __table_args__ = (Index("ix_breaks_session_id", "session_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("work_sessions.id", ondelete="CASCADE")
    )
    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    duration_seconds: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)

    session: Mapped[WorkSession] = relationship(back_populates="breaks")


class ActivityPeriodRecord(Base):
    """Active/idle timeline rows (docs/DATA_MODEL.md ``activity_periods``)."""

    __tablename__ = "activity_periods"
    __table_args__ = (Index("ix_activity_periods_session_id", "session_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("work_sessions.id", ondelete="CASCADE")
    )
    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    state: Mapped[str] = mapped_column(String(16), default=ActivityState.ACTIVE)
    duration_seconds: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)

    session: Mapped[WorkSession] = relationship(back_populates="activity_periods")


class ScreenshotMetadata(Base):
    """Screenshot metadata + outbox state (docs/DATA_MODEL.md ``screenshots``).

    Checksum enables orphan/corruption detection. ``sync_status`` drives the
    queue; deletion happens only after the server confirms persistence
    (docs/ENGINEERING_RULES.md §Data Deletion Rule).
    """

    __tablename__ = "screenshots"
    __table_args__ = (
        Index("ix_screenshots_session_id", "session_id"),
        Index("ix_screenshots_sync_status", "sync_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("work_sessions.id", ondelete="CASCADE")
    )
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime)
    activity_state: Mapped[str] = mapped_column(
        String(16), default=ActivityState.ACTIVE
    )
    file_path: Mapped[str] = mapped_column(String(1024), unique=True)
    file_size: Mapped[int | None] = mapped_column(Integer)  # bytes
    checksum: Mapped[str | None] = mapped_column(String(128))
    sync_status: Mapped[str] = mapped_column(String(16), default=SyncStatus.PENDING)
    attempt_count: Mapped[int] = mapped_column(default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)

    session: Mapped[WorkSession] = relationship(back_populates="screenshots")


class SyncQueueItem(Base):
    """Outbox rows (docs/DATA_MODEL.md ``sync_queue``)."""

    __tablename__ = "sync_queue"
    __table_args__ = (Index("ix_sync_queue_status", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[int] = mapped_column(Integer)
    operation: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default=SyncStatus.PENDING)
    attempt_count: Mapped[int] = mapped_column(default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_error: Mapped[str | None] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class AppStateRow(Base):
    """Misc runtime flags (theme, window position…) — docs/DATA_MODEL.md."""

    __tablename__ = "app_state"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(String(4096))
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utc_now, onupdate=utc_now
    )


class Setting(Base):
    """Local settings / cached server-pushed policy (docs/DATA_MODEL.md)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(String(4096))
