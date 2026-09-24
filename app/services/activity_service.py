"""Activity service — idle detection over persisted periods.

Privacy (docs/ENGINEERING_RULES.md §Privacy boundary): the service never sees
*what* was typed/clicked — it only receives a payload-free tick
(``INPUT_ACTIVITY_DETECTED``) via :meth:`on_activity`. All idle/active math
is a pure projection of persisted ``activity_periods`` rows against the
configured ``idle_threshold``; no timer is accumulated in memory
(docs §Timer Correctness, §Sleep/lock).

Break/checkout aware (docs/STATE_MACHINE.md §BREAK): no periods are written
while the session is on BREAK or after COMPLETED. The worker stops polling
during break — this service enforces the same rule if called regardless.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from app.core.clock import SYSTEM_CLOCK, Clock
from app.core.logging import get_logger
from app.domain.activity.activity import ActivityState
from app.domain.sync.sync import SyncEntityType, SyncOperation
from app.infrastructure.database.repositories import (
    ActivityRepository,
    SyncQueueRepository,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

SessionFactory = Callable[[], "Session"]


class ActivityService:
    """Orchestrates active/idle periods for one work session at a time.

    The service is intentionally state-light: the *authoritative* timeline is
    the persisted ``activity_periods`` rows. In-memory fields
    (``_last_activity_at``, ``_paused``) are a convenience mirror that is
    rebuilt on :meth:`restore` / :meth:`start`.
    """

    def __init__(
        self,
        *,
        session_factory: SessionFactory | None = None,
        clock: Clock = SYSTEM_CLOCK,
        idle_threshold_seconds: int = 300,
    ) -> None:
        if idle_threshold_seconds <= 0:
            raise ValueError("idle threshold must be positive")
        self._session_factory = session_factory
        self._clock = clock
        self._idle_threshold = timedelta(seconds=idle_threshold_seconds)
        self._session_id: int | None = None
        self._last_activity_at: datetime | None = None
        self._paused = False
        self._lock = threading.RLock()
        self._logger = get_logger("activity")

    # ── Configuration ─────────────────────────────────────────────────────

    @property
    def idle_threshold(self) -> timedelta:
        return self._idle_threshold

    def set_idle_threshold(self, seconds: int) -> None:
        if seconds <= 0:
            raise ValueError("idle threshold must be positive")
        with self._lock:
            self._idle_threshold = timedelta(seconds=seconds)

    def set_session_factory(self, factory: SessionFactory | None) -> None:
        self._session_factory = factory

    # ── Session lifecycle ─────────────────────────────────────────────────

    def start(self, session_id: int, *, at: datetime | None = None) -> None:
        """Begin tracking for ``session_id`` (CHECK_IN or restored WORKING).

        Creates an open ACTIVE period at ``at`` if none exists. Idempotent —
        calling twice for the same session is a no-op (recovery-safe).
        """
        at = at or self._clock.utc()
        self._ensure_tz(at)
        with self._lock:
            # Switching sessions: flush the previous one (defensive)
            if self._session_id is not None and self._session_id != session_id:
                self._close_open_locked(self._session_id, at)
            self._session_id = session_id
            self._paused = False
            self._last_activity_at = at

            if self._session_factory is None:
                return

            try:
                with self._session_factory() as db:
                    repo = ActivityRepository(db)
                    open_row = repo.open_for_session(session_id)
                    if open_row is not None:
                        # Recovery: adopt the existing open period
                        self._last_activity_at = open_row.started_at
                        # If open row is IDLE, last activity is start - threshold
                        if open_row.state == ActivityState.IDLE.value:
                            self._last_activity_at = (
                                open_row.started_at - self._idle_threshold
                            )
                        db.commit()
                        return
                    # Check if any periods already exist for session (e.g. after break resume)
                    existing = repo.list_for_session(session_id)
                    if existing:
                        # Session was paused and resumed — start a fresh ACTIVE
                        row = repo.append(
                            session_id=session_id,
                            state=ActivityState.ACTIVE,
                            started_at=at,
                        )
                        self._enqueue_period(db, row.id, SyncOperation.CREATE.value)
                    else:
                        row = repo.append(
                            session_id=session_id,
                            state=ActivityState.ACTIVE,
                            started_at=at,
                        )
                        self._enqueue_period(db, row.id, SyncOperation.CREATE.value)
                    db.commit()
                    self._logger.info(
                        "activity tracking started session=%s at=%s", session_id, at
                    )
            except Exception as exc:
                self._logger.error(
                    "failed to start activity tracking: %s", exc, exc_info=True
                )

    def pause(self, *, at: datetime | None = None) -> None:
        """Pause tracking (BREAK) — closes the open period at ``at``."""
        at = at or self._clock.utc()
        self._ensure_tz(at)
        with self._lock:
            if self._session_id is None or self._paused:
                return
            # Check idle first so we don't leave a stale ACTIVE that should be IDLE
            self._maybe_transition_to_idle_locked(at)
            self._close_open_locked(self._session_id, at)
            self._paused = True
            self._logger.info("activity tracking paused at=%s", at)

    def resume(self, *, at: datetime | None = None) -> None:
        """Resume tracking after BREAK — opens a fresh ACTIVE period."""
        at = at or self._clock.utc()
        self._ensure_tz(at)
        with self._lock:
            if self._session_id is None:
                return
            self._paused = False
            self._last_activity_at = at
            if self._session_factory is None:
                return
            try:
                with self._session_factory() as db:
                    repo = ActivityRepository(db)
                    # Defensive: close any stray open (should already be closed by pause)
                    open_row = repo.open_for_session(self._session_id)
                    if open_row is not None:
                        closed = repo.close_open_for_session(
                            self._session_id, ended_at=at
                        )
                        if closed is not None:
                            self._enqueue_period(
                                db, closed.id, SyncOperation.UPDATE.value
                            )
                    row = repo.append(
                        session_id=self._session_id,
                        state=ActivityState.ACTIVE,
                        started_at=at,
                    )
                    self._enqueue_period(db, row.id, SyncOperation.CREATE.value)
                    db.commit()
                    self._logger.info("activity tracking resumed at=%s", at)
            except Exception as exc:
                self._logger.error(
                    "failed to resume activity tracking: %s", exc, exc_info=True
                )

    def stop(self, *, at: datetime | None = None) -> None:
        """Stop tracking (CHECK_OUT) — closes the open period and clears session."""
        at = at or self._clock.utc()
        self._ensure_tz(at)
        with self._lock:
            if self._session_id is None:
                return
            self._maybe_transition_to_idle_locked(at)
            self._close_open_locked(self._session_id, at)
            self._logger.info(
                "activity tracking stopped session=%s at=%s", self._session_id, at
            )
            self._session_id = None
            self._last_activity_at = None
            self._paused = False

    def restore(self, session_id: int, *, at: datetime | None = None) -> None:
        """Rebuild in-memory mirror from persisted periods (startup recovery)."""
        at = at or self._clock.utc()
        with self._lock:
            self._session_id = session_id
            self._paused = False
            if self._session_factory is None:
                self._last_activity_at = at
                return
            try:
                with self._session_factory() as db:
                    repo = ActivityRepository(db)
                    rows = repo.list_for_session(session_id)
                    open_row = repo.open_for_session(session_id)
                    if open_row is not None:
                        if open_row.state == ActivityState.ACTIVE.value:
                            self._last_activity_at = open_row.started_at
                            # If there were prior periods, try to infer more accurate last activity
                            # by checking if idle transition would have happened
                            rows_closed = [r for r in rows if r.ended_at is not None]
                            if rows_closed:
                                pass
                        else:  # IDLE open
                            self._last_activity_at = (
                                open_row.started_at - self._idle_threshold
                            )
                    elif rows:
                        # No open period — session was paused (BREAK) or just completed
                        last = rows[-1]
                        if last.ended_at is not None:
                            self._last_activity_at = last.ended_at
                            # If last was IDLE, activity was earlier
                            if last.state == ActivityState.IDLE.value:
                                self._last_activity_at = (
                                    last.started_at - self._idle_threshold
                                )
                        self._paused = True
                    else:
                        self._last_activity_at = at
                    db.commit()
            except Exception as exc:
                self._logger.error(
                    "failed to restore activity state: %s", exc, exc_info=True
                )
                self._last_activity_at = at

    def shutdown(self, *, at: datetime | None = None) -> None:
        """App exit — flush open period without clearing session binding."""
        at = at or self._clock.utc()
        self._ensure_tz(at)
        with self._lock:
            if self._session_id is None or self._paused:
                return
            self._maybe_transition_to_idle_locked(at)
            self._close_open_locked(self._session_id, at)

    # ── Activity tick ─────────────────────────────────────────────────────

    def on_activity(self, *, at: datetime | None = None) -> ActivityState | None:
        """Record that input occurred at ``at`` — returns new state if changed."""
        at = at or self._clock.utc()
        self._ensure_tz(at)
        with self._lock:
            if self._session_id is None or self._paused:
                return None
            previous = self._last_activity_at
            self._last_activity_at = at

            if self._session_factory is None:
                return ActivityState.ACTIVE

            try:
                with self._session_factory() as db:
                    repo = ActivityRepository(db)
                    open_row = repo.open_for_session(self._session_id)
                    if open_row is None:
                        # No open period — create ACTIVE (should not happen normally)
                        row = repo.append(
                            session_id=self._session_id,
                            state=ActivityState.ACTIVE,
                            started_at=at,
                        )
                        self._enqueue_period(db, row.id, SyncOperation.CREATE.value)
                        db.commit()
                        return ActivityState.ACTIVE

                    if open_row.state == ActivityState.IDLE.value:
                        # Waking from idle: close IDLE at `at`, open ACTIVE
                        closed = repo.close_open_for_session(
                            self._session_id, ended_at=at
                        )
                        if closed is not None:
                            self._enqueue_period(
                                db, closed.id, SyncOperation.UPDATE.value
                            )
                        row = repo.append(
                            session_id=self._session_id,
                            state=ActivityState.ACTIVE,
                            started_at=at,
                        )
                        self._enqueue_period(db, row.id, SyncOperation.CREATE.value)
                        db.commit()
                        self._logger.info("activity resumed ACTIVE at=%s", at)
                        return ActivityState.ACTIVE

                    # Currently ACTIVE
                    if previous is not None and at - previous >= self._idle_threshold:
                        # Missed idle transition — synthesize it
                        idle_start = previous + self._idle_threshold
                        closed = repo.close_open_for_session(
                            self._session_id, ended_at=idle_start
                        )
                        if closed is not None:
                            self._enqueue_period(
                                db, closed.id, SyncOperation.UPDATE.value
                            )
                        r1 = repo.append(
                            session_id=self._session_id,
                            state=ActivityState.IDLE,
                            started_at=idle_start,
                            ended_at=at,
                        )
                        self._enqueue_period(db, r1.id, SyncOperation.CREATE.value)
                        r2 = repo.append(
                            session_id=self._session_id,
                            state=ActivityState.ACTIVE,
                            started_at=at,
                        )
                        self._enqueue_period(db, r2.id, SyncOperation.CREATE.value)
                        db.commit()
                        return ActivityState.ACTIVE

                    db.commit()
                    return None
            except Exception as exc:
                self._logger.error("failed to record activity: %s", exc, exc_info=True)
                return None

    def check_idle(self, *, at: datetime | None = None) -> ActivityState | None:
        """Poll for idle — transitions ACTIVE→IDLE when threshold exceeded."""
        at = at or self._clock.utc()
        self._ensure_tz(at)
        with self._lock:
            if (
                self._session_id is None
                or self._paused
                or self._last_activity_at is None
            ):
                return None
            return self._maybe_transition_to_idle_locked(at)

    # ── Queries ───────────────────────────────────────────────────────────

    def current_state(self, *, at: datetime | None = None) -> ActivityState:
        """Recomputed state at ``at`` (persisted + threshold projection)."""
        at = at or self._clock.utc()
        self._ensure_tz(at)
        with self._lock:
            if self._session_id is None or self._paused:
                return ActivityState.ACTIVE  # default when not tracking
            if self._last_activity_at is None:
                return ActivityState.ACTIVE
            if at - self._last_activity_at >= self._idle_threshold:
                return ActivityState.IDLE
            # Also check persisted open IDLE (authoritative)
            if self._session_factory is not None:
                try:
                    with self._session_factory() as db:
                        repo = ActivityRepository(db)
                        open_row = repo.open_for_session(self._session_id)
                        if (
                            open_row is not None
                            and open_row.state == ActivityState.IDLE.value
                        ):
                            return ActivityState.IDLE
                        db.commit()
                except Exception:
                    pass
            return ActivityState.ACTIVE

    def get_breakdown(
        self, session_id: int, *, at: datetime | None = None
    ) -> tuple[int, int]:
        """Return (active_seconds, idle_seconds) for ``session_id`` at ``at``.

        Includes the trailing open period projected to ``at`` with idle clipping
        — never accumulates in memory.
        """
        at = at or self._clock.utc()
        self._ensure_tz(at)
        if self._session_factory is None:
            return (0, 0)
        try:
            with self._session_factory() as db:
                repo = ActivityRepository(db)
                rows = repo.list_for_session(session_id)
                db.commit()

                active = 0
                idle = 0
                for row in rows:
                    state = ActivityState(row.state)
                    ended = row.ended_at or at
                    # Clip trailing open ACTIVE at idle boundary if needed
                    if (
                        row.ended_at is None
                        and state is ActivityState.ACTIVE
                        and self._session_id == session_id
                        and self._last_activity_at is not None
                        and at - self._last_activity_at >= self._idle_threshold
                    ):
                        clipped_end = self._last_activity_at + self._idle_threshold
                        active += max(
                            0, int((clipped_end - row.started_at).total_seconds())
                        )
                        idle += max(0, int((at - clipped_end).total_seconds()))
                        continue
                    secs = max(0, int((ended - row.started_at).total_seconds()))
                    if state is ActivityState.ACTIVE:
                        active += secs
                    else:
                        idle += secs
                return (active, idle)
        except Exception as exc:
            self._logger.error("failed to compute breakdown: %s", exc, exc_info=True)
            return (0, 0)

    def list_periods(self, session_id: int) -> list[dict[str, object]]:
        """List periods for session (for tests/debugging)."""
        if self._session_factory is None:
            return []
        try:
            with self._session_factory() as db:
                repo = ActivityRepository(db)
                rows = repo.list_for_session(session_id)
                db.commit()
                return [
                    {
                        "state": row.state,
                        "started_at": row.started_at,
                        "ended_at": row.ended_at,
                        "duration_seconds": row.duration_seconds,
                    }
                    for row in rows
                ]
        except Exception:
            return []

    # ── Internals ─────────────────────────────────────────────────────────

    def _maybe_transition_to_idle_locked(self, at: datetime) -> ActivityState | None:
        if self._last_activity_at is None or self._session_id is None:
            return None
        if at - self._last_activity_at < self._idle_threshold:
            return None
        if self._session_factory is None:
            return ActivityState.IDLE
        try:
            with self._session_factory() as db:
                repo = ActivityRepository(db)
                open_row = repo.open_for_session(self._session_id)
                if open_row is None:
                    db.commit()
                    return None
                if open_row.state == ActivityState.IDLE.value:
                    db.commit()
                    return None
                # ACTIVE → IDLE at last_activity + threshold
                idle_start = self._last_activity_at + self._idle_threshold
                closed = repo.close_open_for_session(
                    self._session_id, ended_at=idle_start
                )
                if closed is not None:
                    self._enqueue_period(db, closed.id, SyncOperation.UPDATE.value)
                row = repo.append(
                    session_id=self._session_id,
                    state=ActivityState.IDLE,
                    started_at=idle_start,
                )
                self._enqueue_period(db, row.id, SyncOperation.CREATE.value)
                db.commit()
                self._logger.info(
                    "idle detected at=%s threshold=%s", at, self._idle_threshold
                )
                return ActivityState.IDLE
        except Exception as exc:
            self._logger.error("failed to transition to idle: %s", exc, exc_info=True)
            return None

    def _close_open_locked(self, session_id: int, at: datetime) -> None:
        if self._session_factory is None:
            return
        try:
            with self._session_factory() as db:
                repo = ActivityRepository(db)
                closed = repo.close_open_for_session(session_id, ended_at=at)
                if closed is not None:
                    self._enqueue_period(db, closed.id, SyncOperation.UPDATE.value)
                db.commit()
        except Exception as exc:
            self._logger.error("failed to close open period: %s", exc, exc_info=True)

    def _enqueue_period(self, db, period_id: int, operation: str) -> None:  # type: ignore[no-untyped-def]
        try:
            q_repo = SyncQueueRepository(db)
            q_repo.enqueue(
                entity_type=SyncEntityType.ACTIVITY_PERIOD.value,
                entity_id=period_id,
                operation=operation,
            )
        except Exception:
            self._logger.debug(
                "failed to enqueue activity_period %s", period_id, exc_info=True
            )

    @staticmethod
    def _ensure_tz(at: datetime) -> None:
        if at.tzinfo is None:
            raise ValueError("activity timestamps must be timezone-aware (UTC)")
