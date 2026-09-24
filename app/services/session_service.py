"""Session orchestration for the UI (Phase 4 projection drive).

Phase 4 owns the premium UI: this service gives the dashboard and tray a
single, in-memory seam to the session state machine so UI code never touches
domain objects directly (layering rule, docs/PROJECT_STRUCTURE.md). It
produces an immutable :class:`SessionView` computed *at call time* from the
machine's persisted-adjacent timestamps — the UI timer is always a fresh
projection, never an accumulated counter (docs/ENGINEERING_RULES.md §Timer
Correctness).

Phase 5 (work-session engine) layers SQLite persistence and restart recovery
behind the same three call surfaces: the state machine already carries the
full transition/session semantics; this service only exposes them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.core.clock import SYSTEM_CLOCK, Clock
from app.core.exceptions import SessionStateError
from app.core.logging import get_logger
from app.domain.activity.activity import ActivityState
from app.domain.sessions.state_machine import AppState, SessionAction, SessionMachine

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session

    from app.domain.sessions.session import WorkSession
    from app.infrastructure.database.repositories import SessionRepository

SessionFactory = Callable[[], "Session"]


@dataclass(frozen=True)
class SessionView:
    """Flat, immutable snapshot the UI renders (see :mod:`app.ui.view_models`)."""

    state: AppState
    activity_state: ActivityState = ActivityState.ACTIVE
    elapsed_work_seconds: int = 0
    elapsed_break_seconds: int = 0
    checked_out_at: datetime | None = None
    active_minutes: int = 0
    idle_minutes: int = 0
    break_minutes: int = 0
    can_check_in: bool = False
    can_take_break: bool = False
    can_resume: bool = False
    can_check_out: bool = False
    ticking: bool = False


class SessionService:
    """Drives the shared :class:`SessionMachine` on behalf of the UI.

    Phase 5: Provides SQLite persistence and restart recovery.
    The service is intentionally thin — state transitions are owned by
    :class:`SessionMachine`; this layer only mirrors them to SQLite so that
    elapsed time remains derivable from persisted timestamps.
    """

    def __init__(
        self,
        *,
        machine: SessionMachine | None = None,
        clock: Clock = SYSTEM_CLOCK,
        session_factory: SessionFactory | None = None,
    ) -> None:
        self._machine = machine or SessionMachine()
        self._clock = clock
        self._user_id: int | None = None
        self._session_factory: SessionFactory | None = session_factory
        self._logger = get_logger("session")
        # Back-compat: some callers still pass repo directly (tests)
        self._session_repository: SessionRepository | None = None

    # Back-compat shim for old wiring (container used repo+factory). Keep it
    # so existing tests don't break if they call set_persistence(repo,factory).
    def set_persistence(
        self,
        session_repository: SessionRepository | None = None,
        session_factory: SessionFactory | None = None,
        **kwargs: object,
    ) -> None:
        if session_repository is not None:
            self._session_repository = session_repository
        if session_factory is not None:
            self._session_factory = session_factory
        if "factory" in kwargs:
            fac = kwargs["factory"]
            if callable(fac):
                from typing import cast

                self._session_factory = cast("SessionFactory", fac)

    def set_session_factory(self, factory: SessionFactory | None) -> None:
        self._session_factory = factory

    def set_user(self, user_id: int | None) -> None:
        """Bind the authenticated user (controllers set this post-login)."""
        self._user_id = user_id

    @property
    def machine(self) -> SessionMachine:
        """The shared app-level machine (also driven by auth)."""
        return self._machine

    @property
    def state(self) -> AppState:
        return self._machine.state

    def can(self, action: SessionAction) -> bool:
        return self._machine.can(action)

    # ── Actions ────────────────────────────────────────────────────────────
    def check_in(self) -> SessionView:
        if self._user_id is None:
            raise SessionStateError("check-in requires an authenticated user")
        at = self._clock.utc()
        self._machine.apply(SessionAction.CHECK_IN, at=at, user_id=self._user_id)
        self._persist_check_in(at)
        return self.tick()

    def take_break(self) -> SessionView:
        at = self._clock.utc()
        self._machine.apply(SessionAction.TAKE_BREAK, at=at)
        self._persist_take_break(at)
        return self.tick()

    def resume(self) -> SessionView:
        at = self._clock.utc()
        self._machine.apply(SessionAction.RESUME, at=at)
        self._persist_resume(at)
        return self.tick()

    def check_out(self) -> SessionView:
        at = self._clock.utc()
        self._machine.apply(SessionAction.CHECK_OUT, at=at)
        self._persist_check_out(at)
        return self.tick()

    # ── Persistence helpers ───────────────────────────────────────────────
    def _persist_check_in(self, at: datetime) -> None:
        if self._session_factory is None:
            return
        session = self._machine.session
        if session is None or session.user_id != self._user_id:
            return
        try:
            with self._session_factory() as db:
                from app.infrastructure.database.repositories import SessionRepository

                repo = SessionRepository(db)
                # If this session already has an ORM id, it was restored — skip create.
                if session.id is not None:
                    existing = repo.get_by_id(session.id)
                    if existing is not None:
                        db.commit()
                        return
                row = repo.create(user_id=session.user_id, started_at=at)
                db.commit()
                # sync ORM id back to domain
                session.id = row.id
                self._logger.info("check-in id=%s user=%s", row.id, row.user_id)
        except Exception as exc:
            self._logger.error("failed to persist check-in: %s", exc, exc_info=True)

    def _persist_take_break(self, at: datetime) -> None:
        if self._session_factory is None:
            return
        session = self._machine.session
        if session is None or session.id is None:
            return
        try:
            with self._session_factory() as db:
                from app.domain.sessions.session import WorkSessionStatus
                from app.infrastructure.database.repositories import (
                    BreakRepository,
                    SessionRepository,
                )

                s_repo = SessionRepository(db)
                b_repo = BreakRepository(db)
                s_repo.update_status(session.id, WorkSessionStatus.BREAK)
                b_repo.start(session_id=session.id, started_at=at)
                db.commit()
                # assign id to last break if missing
                try:
                    rows = b_repo.list_for_session(session.id)
                    for br in rows:
                        if br.started_at == at and br.ended_at is None:
                            if session.breaks:
                                last = session.breaks[-1]
                                if last.id is None:
                                    from dataclasses import replace

                                    session.breaks[-1] = replace(last, id=br.id)
                            break
                except Exception:
                    self._logger.debug("break id sync skipped", exc_info=True)
                self._logger.info("persisted take_break id=%s at=%s", session.id, at)
        except Exception as exc:
            self._logger.error("failed to persist take_break: %s", exc, exc_info=True)

    def _persist_resume(self, at: datetime) -> None:
        if self._session_factory is None:
            return
        session = self._machine.session
        if session is None or session.id is None:
            return
        try:
            with self._session_factory() as db:
                from app.domain.sessions.session import WorkSessionStatus
                from app.infrastructure.database.repositories import (
                    BreakRepository,
                    SessionRepository,
                )

                s_repo = SessionRepository(db)
                b_repo = BreakRepository(db)
                open_brk = b_repo.open_for_session(session.id)
                if open_brk is not None:
                    b_repo.end_open(open_brk.id, ended_at=at)
                s_repo.update_status(session.id, WorkSessionStatus.WORKING)
                db.commit()
                self._logger.info("persisted resume id=%s at=%s", session.id, at)
        except Exception as exc:
            self._logger.error("failed to persist resume: %s", exc, exc_info=True)

    def _persist_check_out(self, at: datetime) -> None:
        if self._session_factory is None:
            return
        session = self._machine.session
        if session is None or session.id is None:
            return
        # If a break is still open (checkout from BREAK), close it first
        try:
            with self._session_factory() as db:
                from app.infrastructure.database.repositories import (
                    BreakRepository,
                    SessionRepository,
                )

                b_repo = BreakRepository(db)
                s_repo = SessionRepository(db)
                open_brk = b_repo.open_for_session(session.id)
                if open_brk is not None:
                    b_repo.end_open(open_brk.id, ended_at=at)
                # total_work_seconds already computed by domain checkout()
                total = session.total_work_seconds
                s_repo.checkout(session.id, ended_at=at, total_work_seconds=total)
                db.commit()
                self._logger.info(
                    "persisted checkout session_id=%s total=%s", session.id, total
                )
        except Exception as exc:
            self._logger.error("failed to persist checkout: %s", exc, exc_info=True)

    def restore_session(self) -> bool:
        """Load the persisted active session from the database on startup.

        Returns True if a persisted session was found and restored, False otherwise.
        """
        if self._user_id is None or self._session_factory is None:
            return False
        try:
            with self._session_factory() as db:
                from app.infrastructure.database.repositories import SessionRepository

                repo = SessionRepository(db)
                row = repo.get_active(self._user_id)
                if row is None:
                    return False
                # force load breaks while session alive
                _ = row.breaks
                domain = self._orm_to_domain(row)
                self._machine.restore(domain)
                self._logger.info(
                    "session restored: uid=%s sid=%s status=%s",
                    domain.user_id,
                    domain.id,
                    domain.status,
                )
                return True
        except Exception as exc:
            self._logger.error(
                "failed to restore session: user_id=%s error=%s",
                self._user_id,
                exc,
                exc_info=True,
            )
            return False

    @staticmethod
    def _orm_to_domain(row: Any) -> WorkSession:
        """Map ORM WorkSession row (+ its BreakRecords) to domain WorkSession."""
        from typing import cast

        from app.domain.sessions.session import Break, WorkSession, WorkSessionStatus

        breaks = [
            Break(
                session_id=cast("Any", br).session_id,
                started_at=cast("Any", br).started_at,
                ended_at=cast("Any", br).ended_at,
                duration_seconds=cast("Any", br).duration_seconds or 0,
                id=cast("Any", br).id,
            )
            for br in getattr(row, "breaks", []) or []
        ]
        breaks.sort(key=lambda b: b.started_at)
        return WorkSession(
            user_id=cast("Any", row).user_id,
            started_at=cast("Any", row).started_at,
            status=WorkSessionStatus(cast("Any", row).status),
            id=cast("Any", row).id,
            ended_at=cast("Any", row).ended_at,
            total_work_seconds=cast("Any", row).total_work_seconds or 0,
            breaks=breaks,
        )

    # ── Projection ─────────────────────────────────────────────────────────
    def tick(self) -> SessionView:
        """Recompute the current snapshot from machine/persisted timestamps."""
        machine = self._machine
        session = machine.session
        state = machine.state
        now = self._clock.utc()

        view = SessionView(
            state=state,
            can_check_in=machine.can(SessionAction.CHECK_IN),
            can_take_break=machine.can(SessionAction.TAKE_BREAK),
            can_resume=machine.can(SessionAction.RESUME),
            can_check_out=machine.can(SessionAction.CHECK_OUT),
            ticking=state in (AppState.WORKING, AppState.BREAK),
        )

        if state in (AppState.READY, AppState.LOGGED_OUT):
            return view
        if session is None:
            return view

        if state is AppState.COMPLETED:
            total = session.total_work_seconds
            break_seconds = session.accumulated_break_seconds
            return SessionView(
                state=state,
                elapsed_work_seconds=total,
                checked_out_at=session.ended_at,
                active_minutes=total // 60,
                idle_minutes=0,  # activity engine (Phase 6) fills this
                break_minutes=break_seconds // 60,
                can_check_in=machine.can(SessionAction.CHECK_IN),
                can_take_break=False,
                can_resume=False,
                can_check_out=False,
                ticking=False,
            )

        if state is AppState.WORKING:
            return SessionView(
                state=state,
                elapsed_work_seconds=session.elapsed_work_seconds(now),
                can_check_in=False,
                can_take_break=machine.can(SessionAction.TAKE_BREAK),
                can_resume=False,
                can_check_out=machine.can(SessionAction.CHECK_OUT),
                ticking=True,
            )

        # BREAK: show the running break duration (open-most break).
        open_break_seconds = self._open_break_elapsed(session, now)
        return SessionView(
            state=state,
            elapsed_work_seconds=session.elapsed_work_seconds(now),
            elapsed_break_seconds=open_break_seconds,
            can_check_in=False,
            can_take_break=False,
            can_resume=machine.can(SessionAction.RESUME),
            can_check_out=machine.can(SessionAction.CHECK_OUT),
            ticking=True,
        )

    @staticmethod
    def _open_break_elapsed(session: WorkSession, now: datetime) -> int:
        for brk in reversed(session.breaks):
            if brk.is_open:
                started = brk.started_at
                if started.tzinfo is None:
                    return 0  # defensive: never underflow on naive timestamps
                return max(0, int((now - started).total_seconds()))
        return 0


__all__ = ["SessionService", "SessionView"]
