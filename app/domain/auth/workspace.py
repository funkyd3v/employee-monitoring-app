"""The workspace profile — the label the app shows for the signed-in employee.

Split out of :mod:`app.domain.auth.auth` because it is not part of
authentication: the identity (who you are) is established once at login, while
the workspace (what this machine calls itself for you) can be renamed by the
employee from the dashboard at any moment and must be re-read while the app is
running.

That separation is what lets the UI update a label without touching the login
flow, and it mirrors how the backend splits ``/me`` (identity) from
``/workspace`` (profile) — one column, two front doors.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.domain.auth.auth import AuthenticatedUser


@dataclass(frozen=True, slots=True)
class WorkspaceProfile:
    """What the UI needs to label the app, plus what it last saw."""

    workspace_name: str | None
    #: Where the value came from, for the "last updated" hint. Not persisted;
    #: purely presentational.
    source: str = "local"


class WorkspaceProvider(ABC):
    """Contract for reading the current workspace profile."""

    @abstractmethod
    def fetch_workspace(self) -> AuthenticatedUser | None:
        """Return the current identity, or None when it cannot be read.

        Implementations must never raise for an ordinary network or
        authentication failure: a stale label is cosmetic, and the caller
        (a background sync tick) has no way to recover from an exception.
        """


class LocalWorkspaceProvider(WorkspaceProvider):
    """The ``mode: "local"`` implementation: nothing to fetch.

    A local run has no server and therefore no remote workspace; returning
    None means the UI keeps whatever the local settings say, which is exactly
    the pre-backend behaviour.
    """

    def fetch_workspace(self) -> AuthenticatedUser | None:
        return None


__all__ = ["LocalWorkspaceProvider", "WorkspaceProfile", "WorkspaceProvider"]
