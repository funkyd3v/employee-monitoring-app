"""Application exception hierarchy.

Services and domains raise typed exceptions; the UI maps them to user-facing
messages (never raw exception text). The hierarchy keeps catch-and-convert
logic in one place as the app grows.
"""

from __future__ import annotations


class AppError(Exception):
    """Base class for all application errors."""


class ConfigurationError(AppError):
    """Raised when settings are missing or invalid at bootstrap."""


class DataIntegrityError(AppError):
    """Raised when persisted state contradicts expectations (orphans,
    corrupted records). Recovery/reconciliation logic should handle these
    rather than the app crashing."""


class AuthenticationError(AppError):
    """Raised when authentication or session restoration fails."""


class InvalidCredentialsError(AuthenticationError):
    """The email/password pair was refused, or a stored token is no longer valid.

    Split out from its parent because the employee can act on it — retype the
    password — and because the UI must not tell someone to check their password
    when the actual problem was that the server could not be reached.
    """


class MonitoringServerUnavailableError(AuthenticationError):
    """The monitoring backend could not be reached, or answered unusably.

    Also actionable, differently: this is a connection or server problem, so
    retrying with the same credentials will fail the same way.
    """


class SessionStateError(AppError):
    """Raised on invalid state-machine transitions (declared in
    docs/STATE_MACHINE.md — the machine itself rejects invalid transitions,
    independent of the UI)."""


class SyncError(AppError):
    """Raised when a sync attempt fails; the retry/backoff policy owns
    recovery."""


class ProviderUnavailableError(AppError):
    """Raised when a backend/OS provider cannot be reached (activity,
    screenshots, auth). Monitoring must continue without crashing."""
