"""Authentication orchestration service.

Sits between the UI / lifecycle and the auth/provider pieces
(``docs/ARCHITECTURE.md § Application lifecycle``): it owns *how* a login,
restore, or logout is carried out without knowing which :class:`AuthProvider`
or :class:`CredentialStore` is wired in (the container decides). It drives
the session state machine's LOGIN/LOGOUT transitions and persists the
authenticated user into the local ``users`` cache (docs/DATA_MODEL.md).

Security invariants (docs/SECURITY_PRIVACY.md):
* The token is never logged; it is registered with the log-redaction
  boundary on load and discarded on logout/shutdown.
* Passwords only ever flow into the provider's ``login`` call and never
  appear in log messages or stored state.
* When secure token storage is unavailable, login fails loudly rather than
  persisting the token anywhere insecure.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.core.clock import SYSTEM_CLOCK, Clock
from app.core.exceptions import AuthenticationError
from app.core.logging import discard_secret, get_logger, register_secret
from app.domain.auth.credential_store import (
    CredentialStore,
    CredentialStoreUnavailableError,
)
from app.domain.sessions.state_machine import (
    AppState,
    SessionAction,
    SessionMachine,
)
from app.infrastructure.database.repositories import UserRepository

if TYPE_CHECKING:
    from app.domain.auth.auth import AuthenticatedUser, AuthProvider
    from app.infrastructure.database.models import User

SessionFactory = Callable[[], Session]


class AuthService:
    """Login / logout / restore over a provider, credential store, and DB."""

    def __init__(
        self,
        *,
        auth_provider: AuthProvider,
        credential_store: CredentialStore,
        session_factory: SessionFactory,
        machine: SessionMachine | None = None,
        clock: Clock = SYSTEM_CLOCK,
    ) -> None:
        self._provider = auth_provider
        self._store = credential_store
        self._session_factory = session_factory
        self._machine = machine or SessionMachine()
        self._clock = clock
        self._logger = get_logger("auth")
        self._token: str | None = None
        self._current_user: AuthenticatedUser | None = None
        self._current_user_id: int | None = None

    @property
    def state(self) -> AppState:
        return self._machine.state

    @property
    def machine(self) -> SessionMachine:
        """The app-level machine; session services drive it in later phases."""
        return self._machine

    def current_user(self) -> AuthenticatedUser | None:
        return self._current_user

    def current_user_id(self) -> int | None:
        return self._current_user_id

    def is_authenticated(self) -> bool:
        return self._current_user is not None

    def login(self, email: str, password: str) -> AuthenticatedUser:
        """Authenticate, persist the session securely, and go READY.

        Raises :class:`AuthenticationError` on bad credentials or when the
        session token cannot be persisted securely.
        """
        session = self._provider.login(email, password)
        token = session.token
        user = session.user
        register_secret(token)
        try:
            self._store.save(token)
        except CredentialStoreUnavailableError as exc:
            discard_secret(token)
            raise AuthenticationError(
                "authenticated but the session token could not be persisted "
                "securely; please check the credential store"
            ) from exc

        try:
            row = self._upsert_user(user)
        except Exception:
            # Roll back the persisted token so a failed login can't silently
            # restore a half-set-up session on the next start.
            discard_secret(token)
            with suppress(CredentialStoreUnavailableError):
                self._store.clear()
            raise

        self._token = token
        self._current_user = user
        self._current_user_id = row.id
        self._machine.apply(SessionAction.LOGIN, at=self._clock.utc())
        self._logger.info("user authenticated (email=%s)", user.email)
        return user

    def logout(self) -> None:
        """End the session: revoke, wipe the stored token, go LOGGED_OUT.

        Runs the state-machine LOGIN/LOGOUT rule first, so a logout attempt
        while WORKING/BREAK raises :class:`SessionStateError` and the stored
        credentials stay intact (the UI must check out first).
        """
        self._machine.apply(SessionAction.LOGOUT, at=self._clock.utc())
        token = self._token
        if token is not None:
            try:
                self._provider.logout(token)
            except Exception:
                self._logger.warning(
                    "logout revocation failed (best-effort)", exc_info=True
                )
            try:
                self._store.clear()
            except CredentialStoreUnavailableError:
                self._logger.error(
                    "could not clear stored session token — logging out "
                    "cleanly despite that; a stale token may survive this run"
                )
            discard_secret(token)
        self._token = None
        self._current_user = None
        self._current_user_id = None
        self._logger.info("user logged out")

    def restore(self) -> AuthenticatedUser | None:
        """Bootstrap restore (docs/ARCHITECTURE.md § Application lifecycle).

        Loads the persisted token, validates/refreshes it, re-caches the
        user, and returns it (READY). Returns None when there is no token or
        it is no longer valid (stale tokens are wiped), leaving the app
        LOGGED_OUT.
        """
        if self._current_user is not None:
            return self._current_user

        token = self._store.load()
        if not token:
            return None
        try:
            session = self._provider.refresh(token)
        except AuthenticationError:
            self._logger.info("stored session token invalid — discarding")
            with suppress(CredentialStoreUnavailableError):
                self._store.clear()
            discard_secret(token)
            return None

        register_secret(token)
        row = self._upsert_user(session.user)
        self._token = token
        self._current_user = session.user
        self._current_user_id = row.id
        self._machine.apply(SessionAction.LOGIN, at=self._clock.utc())
        self._logger.info("authentication restored (email=%s)", session.user.email)
        return session.user

    def shutdown(self) -> None:
        """Drop the held secret at app exit so logs never reference it."""
        if self._token is not None:
            discard_secret(self._token)
            self._token = None

    def _upsert_user(self, user: AuthenticatedUser) -> User:
        with self._session_factory() as session:
            row = UserRepository(session).upsert(
                external_user_id=user.external_user_id,
                email=user.email,
                display_name=user.display_name,
                team_name=user.team_name,
            )
            session.commit()
            return row
