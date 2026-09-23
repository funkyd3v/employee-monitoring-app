"""Credential store contract — where the auth token lives.

Security invariant (docs/SECURITY_PRIVACY.md): authentication/session tokens
are stored *only* via ``keyring``/the OS credential manager — never in
SQLite, config files, or logs. :class:`CredentialStore` is the seam between
the auth service and whatever secure storage the platform provides
(Windows Credential Manager on the target; guarded alternatives on dev).

Implementations live in ``app/infrastructure/security/credential_store.py``
and are chosen by the container via a backend flag (docs/SECURITY_PRIVACY.md,
``settings.credential_backend``). Services depend on this protocol only.
"""

from __future__ import annotations

from typing import Protocol

from app.core.exceptions import AppError


class CredentialStoreUnavailableError(AppError):
    """Raised when secure token storage cannot be reached.

    Callers must treat this as "tokens cannot be persisted securely" and
    degrade explicitly (start logged out / refuse to store) rather than
    writing the token somewhere insecure.
    """


class CredentialStore(Protocol):
    """Persist/load/clear the opaque auth session token."""

    backend: str

    def save(self, token: str) -> None:
        """Store the token securely (raises on failure, never falls back)."""

    def load(self) -> str | None:
        """Return the stored token, or None when none is stored."""

    def clear(self) -> None:
        """Remove the stored token. Idempotent — no-op when none stored."""
