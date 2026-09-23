"""Session state machine (docs/STATE_MACHINE.md).

```
LOGGED_OUT ──login──▶ READY ──check-in──▶ WORKING ──check-out──▶ COMPLETED
                          ▲                  │▲
                          │                  ││ take-break
                          │                  ▼│
                          └─────logout──── BREAK ──resume──▶ WORKING
```

Invalid transitions are explicitly rejected here — not merely prevented by
UI flow. :class:`SessionStateError` is raised on any illegal action.

The machine owns the current :class:`WorkSession`; on CHECK_IN it creates a
new distinct session; on restart :meth:`restore` sets state from a
persisted unfinished session (docs/ENGINEERING_RULES.md §7 — persisted
state drives recovery, not memory).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from app.core.exceptions import SessionStateError
from app.domain.sessions.session import Break, WorkSession, WorkSessionStatus

if TYPE_CHECKING:
    from datetime import datetime


class AppState(StrEnum):
    """Top-level application state (docs/STATE_MACHINE.md)."""

    LOGGED_OUT = "LOGGED_OUT"
    READY = "READY"  # checked out, dashboard visible, "Check In" shown
    WORKING = "WORKING"  # ACTIVE|IDLE is a sub-state, not this machine's concern
    BREAK = "BREAK"
    COMPLETED = "COMPLETED"


class SessionAction(StrEnum):
    """User/system actions that drive transitions."""

    LOGIN = "LOGIN"
    LOGOUT = "LOGOUT"
    CHECK_IN = "CHECK_IN"
    CHECK_OUT = "CHECK_OUT"
    TAKE_BREAK = "TAKE_BREAK"
    RESUME = "RESUME"


_TRANSITIONS: dict[tuple[AppState, SessionAction], AppState] = {
    (AppState.LOGGED_OUT, SessionAction.LOGIN): AppState.READY,
    (AppState.READY, SessionAction.CHECK_IN): AppState.WORKING,
    (AppState.READY, SessionAction.LOGOUT): AppState.LOGGED_OUT,
    (AppState.WORKING, SessionAction.CHECK_OUT): AppState.COMPLETED,
    (AppState.WORKING, SessionAction.TAKE_BREAK): AppState.BREAK,
    (AppState.BREAK, SessionAction.RESUME): AppState.WORKING,
    (AppState.BREAK, SessionAction.CHECK_OUT): AppState.COMPLETED,
    (AppState.COMPLETED, SessionAction.CHECK_IN): AppState.WORKING,
    (AppState.COMPLETED, SessionAction.LOGOUT): AppState.LOGGED_OUT,
}


@dataclass
class SessionMachine:
    """Mutable holder of the current :class:`AppState` and session.

    ``can(action)`` lets the UI enable/disable buttons; ``apply(action)``
    performs the transition and raises :class:`SessionStateError` when
    invalid — the machine itself rejects, independent of the UI.
    """

    state: AppState = AppState.LOGGED_OUT
    session: WorkSession | None = None

    def can(self, action: SessionAction) -> bool:
        return (self.state, action) in _TRANSITIONS

    def available_actions(self) -> frozenset[SessionAction]:
        return frozenset(
            action for (state, action) in _TRANSITIONS if state is self.state
        )

    def next_state(self, action: SessionAction) -> AppState:
        """Pure transition: return the target state for ``action``.

        Raises :class:`SessionStateError` when the transition is not allowed.
        This never touches the session — it is the machine's rejection
        backbone, used by both :meth:`apply` and by tests/UI projections.
        """
        key = (self.state, action)
        if key not in _TRANSITIONS:
            raise SessionStateError(
                "invalid transition: "
                f"{self.state.value} --{action.value}--> (rejected by machine)"
            )
        return _TRANSITIONS[key]

    def restore(self, session: WorkSession) -> AppState:
        """Adopt a persisted session (bootstrap/restart recovery).

        A COMPLETED session post-dates the last check-out; the app returns
        to READY with its final summary visible. An unfinished session puts
        the app straight back where it was (docs §7).
        """
        self.session = session
        by_status = {
            WorkSessionStatus.WORKING: AppState.WORKING,
            WorkSessionStatus.BREAK: AppState.BREAK,
            WorkSessionStatus.COMPLETED: AppState.READY,
        }
        self.state = by_status[session.status]
        return self.state

    def apply(
        self,
        action: SessionAction,
        *,
        at: datetime,
        user_id: int | None = None,
    ) -> AppState:
        """Apply ``action`` at wall-clock time ``at`` and return new state.

        Validates via :meth:`next_state`, then performs the session
        side-effects (distinct new session on CHECK_IN, break open/close,
        checkout math). Raises :class:`SessionStateError` for invalid
        transitions.
        """
        new_state = self.next_state(action)
        session = self.session

        if action is SessionAction.LOGIN:
            self.session = None  # a fresh login clears any prior summary
        elif action is SessionAction.LOGOUT:
            self.session = None
        elif action is SessionAction.CHECK_IN:
            if session is None or session.status is WorkSessionStatus.COMPLETED:
                # Default policy: a fresh Check-In starts a distinct session
                # (a COMPLETED session stays as the visible summary).
                if user_id is None:
                    raise SessionStateError(
                        "check-in requires a logged-in user (user_id)"
                    )
                session = WorkSession(
                    user_id=user_id,
                    started_at=at,
                    status=WorkSessionStatus.WORKING,
                )
                self.session = session
        elif action is SessionAction.CHECK_OUT:
            if session is None:
                raise SessionStateError("cannot check out with no active session")
            session.checkout(at)
            session.status = WorkSessionStatus.COMPLETED
        elif action is SessionAction.TAKE_BREAK:
            if session is None:
                raise SessionStateError("cannot break with no active session")
            session.breaks.append(Break(session_id=session.id or 0, started_at=at))
            session.status = WorkSessionStatus.BREAK
        elif action is SessionAction.RESUME and session is not None:
            self._close_open_break(session, at)
            session.status = WorkSessionStatus.WORKING

        self.state = new_state
        return self.state

    @staticmethod
    def _close_open_break(session: WorkSession, at: datetime) -> None:
        for i, brk in enumerate(session.breaks):
            if brk.is_open:
                session.breaks[i] = brk.close(at)
                return


def is_valid(state: AppState, action: SessionAction) -> bool:
    """Pure predicate used by tests and UI button-state projection."""
    return (state, action) in _TRANSITIONS
