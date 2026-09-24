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

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.core.clock import SYSTEM_CLOCK, Clock
from app.core.exceptions import SessionStateError
from app.domain.activity.activity import ActivityState
from app.domain.sessions.state_machine import AppState, SessionAction, SessionMachine

if TYPE_CHECKING:
    from datetime import datetime

    from app.domain.sessions.session import WorkSession


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
    """Drives the shared :class:`SessionMachine` on behalf of the UI."""

    def __init__(
        self,
        *,
        machine: SessionMachine | None = None,
        clock: Clock = SYSTEM_CLOCK,
    ) -> None:
        self._machine = machine or SessionMachine()
        self._clock = clock
        self._user_id: int | None = None

    @property
    def machine(self) -> SessionMachine:
        """The shared app-level machine (also driven by auth)."""
        return self._machine

    @property
    def state(self) -> AppState:
        return self._machine.state

    def can(self, action: SessionAction) -> bool:
        return self._machine.can(action)

    # ── Identity ───────────────────────────────────────────────────────────
    def set_user(self, user_id: int | None) -> None:
        """Bind the authenticated user (controllers set this post-login)."""
        self._user_id = user_id

    # ── Actions ────────────────────────────────────────────────────────────
    def check_in(self) -> SessionView:
        if self._user_id is None:
            raise SessionStateError("check-in requires an authenticated user")
        self._machine.apply(
            SessionAction.CHECK_IN, at=self._clock.utc(), user_id=self._user_id
        )
        return self.tick()

    def take_break(self) -> SessionView:
        self._machine.apply(SessionAction.TAKE_BREAK, at=self._clock.utc())
        return self.tick()

    def resume(self) -> SessionView:
        self._machine.apply(SessionAction.RESUME, at=self._clock.utc())
        return self.tick()

    def check_out(self) -> SessionView:
        self._machine.apply(SessionAction.CHECK_OUT, at=self._clock.utc())
        return self.tick()

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
