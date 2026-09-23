"""Authentication domain: provider contract and the local dummy provider.

Backend-readiness (docs/ARCHITECTURE.md §Backend-readiness): everything that
will eventually talk to a backend sits behind an interface. UI and services
depend on :class:`AuthProvider`; only the container decides between
:class:`LocalDummyAuthProvider` (today) and a future ``ApiAuthProvider``.

Security invariants (docs/SECURITY_PRIVACY.md):
* No password is ever stored — the dummy compares against the dev-only
  settings value in memory and the comparison is constant-time
  (``secrets.compare_digest``).
* The token is an opaque session handle: it is treated as a live secret
  (registered for log redaction), never logged, and never compared outside
  the provider. The dummy token is self-describing so ``refresh`` can
  reconstruct the session after an app restart without a server; real
  validation arrives with the backend.
"""

from __future__ import annotations

import base64
import json
import secrets
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.core.exceptions import AuthenticationError

_DUMMY_TOKEN_PREFIX = "dv1."  # noqa: S105 -- token format prefix, not a credential


@dataclass(frozen=True)
class AuthenticatedUser:
    """The authenticated identity surfaced to the UI and stored locally."""

    external_user_id: str
    email: str
    display_name: str | None = None
    team_name: str | None = None


@dataclass(frozen=True)
class AuthSession:
    """Result of a login/refresh: the user plus its opaque session token."""

    user: AuthenticatedUser
    token: str


@dataclass(frozen=True)
class DummyAuthConfig:
    """Dev-only login credentials (docs/TESTING_AND_DOD.md §Dummy config).

    ``password`` is overridable via settings for acceptance testing; when it
    is empty (unset), the dummy accepts any non-empty password so developers
    are not forced to hardcode a secret. This is explicitly not a real
    credential store — nothing is persisted here.
    """

    email: str = "employee@example.com"
    password: str = ""
    display_name: str | None = None
    team_name: str | None = None


class AuthProvider(ABC):
    """Contract every auth backend implements (dummy today, API later)."""

    @abstractmethod
    def login(self, email: str, password: str) -> AuthSession:
        """Authenticate and return a session; raises AuthenticationError."""

    @abstractmethod
    def refresh(self, token: str) -> AuthSession:
        """Validate/refresh a persisted token into a session at startup.

        Must not raise on an unknown-but-unambiguous failure before the
        caller decides how to treat it — it raises AuthenticationError for
        malformed/invalid tokens.
        """

    @abstractmethod
    def logout(self, token: str) -> None:
        """Best-effort revocation of ``token`` (no-op in the dummy)."""


def _encode_user(user: AuthenticatedUser) -> str:
    payload = json.dumps(
        {
            "external_user_id": user.external_user_id,
            "email": user.email,
            "display_name": user.display_name,
            "team_name": user.team_name,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    return f"{_DUMMY_TOKEN_PREFIX}{encoded}"


def _decode_user(token: str) -> AuthenticatedUser:
    if not token.startswith(_DUMMY_TOKEN_PREFIX):
        raise AuthenticationError("invalid local session token")
    body = token[len(_DUMMY_TOKEN_PREFIX) :]
    encoded = body + "=" * (-len(body) % 4)
    try:
        payload = json.loads(
            base64.urlsafe_b64decode(encoded.encode("ascii")).decode("utf-8")
        )
    except (ValueError, UnicodeDecodeError) as exc:
        raise AuthenticationError("invalid local session token") from exc
    email = payload.get("email")
    external_user_id = payload.get("external_user_id")
    if not isinstance(email, str) or not isinstance(external_user_id, str):
        raise AuthenticationError("invalid local session token")
    return AuthenticatedUser(
        external_user_id=external_user_id,
        email=email,
        display_name=payload.get("display_name"),
        team_name=payload.get("team_name"),
    )


class LocalDummyAuthProvider(AuthProvider):
    """Dev/dummy implementation selected by ``mode: "local"``.

    Never a production credential path: it has no server, no vault, and its
    token carries no real authority — it exists so the whole auth flow
    (login, persisted session, restore, logout) is exercised end to end and
    the UI/domain never learns which provider is active.
    """

    def __init__(self, config: DummyAuthConfig | None = None) -> None:
        self._config = config or DummyAuthConfig()

    def login(self, email: str, password: str) -> AuthSession:
        if not email or not password:
            raise AuthenticationError("invalid credentials")
        if email != self._config.email:
            raise AuthenticationError("invalid credentials")
        configured_password = self._config.password
        if configured_password and not secrets.compare_digest(
            password, configured_password
        ):
            raise AuthenticationError("invalid credentials")
        user = AuthenticatedUser(
            external_user_id=f"local:{email}",
            email=email,
            display_name=self._config.display_name,
            team_name=self._config.team_name,
        )
        token = _encode_user(user)
        return AuthSession(user=user, token=token)

    def refresh(self, token: str) -> AuthSession:
        # The dummy token is self-describing; structural validation is all a
        # local stand-in can do — a real backend would rotate/validate it.
        user = _decode_user(token)
        return AuthSession(user=user, token=token)

    def logout(self, token: str) -> None:  # noqa: ARG002
        # Nothing server-side to revoke; invoked for interface symmetry and
        # so the future API provider slots in without touching the service.
        return None
