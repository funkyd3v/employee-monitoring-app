"""UI-facing projections and presenter ports.

These are *what the UI renders* — flat, immutable views with zero business
logic, derived by services. Keeping them here (and out of the windows) makes
state-panel rendering and pytest-qt tests trivial, and keeps windows from
depending on domain internals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from app.services.session_service import SessionView


@dataclass(frozen=True)
class DashboardUser:
    """Authenticated identity as the dashboard renders it."""

    display_name: str
    email: str
    team_name: str | None = None

    @property
    def full_label(self) -> str:
        return self.display_name or self.email

    @property
    def first_name(self) -> str:
        return self.full_label.split()[0] if self.full_label else self.email

    @property
    def initials(self) -> str:
        words = [w for w in self.full_label.split() if w]
        if not words:
            return self.email[:1].upper() if self.email else "?"
        if len(words) >= 2:
            return (words[0][0] + words[-1][0]).upper()
        return words[0][:2].upper()


class SessionPresenter(Protocol):
    """The surface the dashboard calls to act on / re-project the session.

    The container wires the concrete
    :class:`~app.services.session_service.SessionService` here; UI tests
    can substitute a stub with the same shape.
    """

    def tick(self) -> SessionView: ...

    def check_in(self) -> SessionView: ...

    def take_break(self) -> SessionView: ...

    def resume(self) -> SessionView: ...

    def check_out(self) -> SessionView: ...
