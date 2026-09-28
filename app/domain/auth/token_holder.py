"""In-memory holder for the live session token.

Backend-readiness (docs/ARCHITECTURE.md §Backend-readiness): both API
providers need the bearer token, but only :class:`AuthService` learns about
authentication. This holder is the seam: the auth service publishes the token
it just persisted, and the sync provider reads it without the sync service
ever importing an auth type.

Security invariants (docs/SECURITY_PRIVACY.md):

* The token lives here in memory only. The credential store remains the single
  durable copy (OS keyring); this is a cache, never a fallback, and nothing
  here is written to disk or logged.
* :meth:`clear` is called on logout and shutdown so the secret does not
  outlive the session it belongs to.
* Thread-safe: the auth service writes from the UI/login thread while the sync
  worker reads from its own thread.
"""

from __future__ import annotations

import threading


class AuthTokenHolder:
    """Thread-safe holder for the current session token."""

    def __init__(self) -> None:
        self._token: str | None = None
        self._lock = threading.Lock()

    def set(self, token: str) -> None:
        with self._lock:
            self._token = token

    def get(self) -> str | None:
        with self._lock:
            return self._token

    def clear(self) -> None:
        with self._lock:
            self._token = None

    def has_token(self) -> bool:
        return self.get() is not None


__all__ = ["AuthTokenHolder"]
