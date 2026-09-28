"""``ApiAuthProvider`` — real backend authentication over HTTP.

Backend-readiness (docs/ARCHITECTURE.md §Backend-readiness): the UI and
:class:`~app.services.auth_service.AuthService` depend on
:class:`~app.domain.auth.auth.AuthProvider`; only the container decides that
``mode: "api"`` means this implementation instead of
:class:`~app.domain.auth.auth.LocalDummyAuthProvider`.

Security invariants (docs/SECURITY_PRIVACY.md):

* The password is used for exactly one HTTPS request and never stored,
  logged, or attached to an error message.
* The returned token is a live secret. It is handed straight to
  :class:`AuthService`, which registers it for log redaction and persists it
  in the OS keyring — this provider keeps no copy.
* The bearer token never appears in a log line or an exception message; only
  the HTTP status is reported.
"""

from __future__ import annotations

import platform
import socket
from typing import TYPE_CHECKING, Any

from app.config.constants import APP_VERSION
from app.core.exceptions import AuthenticationError
from app.core.logging import get_logger
from app.domain.auth.auth import AuthenticatedUser, AuthProvider, AuthSession
from app.domain.auth.workspace import WorkspaceProvider
from app.infrastructure.network.contract import DEFAULT_CONTRACT, ApiContract
from app.infrastructure.network.http_client import (
    ApiAuthError,
    ApiError,
    ApiHttpClient,
)

if TYPE_CHECKING:
    from app.domain.auth.token_holder import AuthTokenHolder

_logger = get_logger("api.auth")


def _device_fingerprint() -> dict[str, str]:
    """Best-effort machine identification for the operator's token list.

    Never contains anything about the user or their input — just what this
    machine calls itself, so two employees on different PCs are tellable
    apart. Every field is optional: a locked-down host may refuse all of it.
    """
    try:
        hostname = socket.gethostname()
    except OSError:
        hostname = ""

    return {
        "device_name": hostname[:255],
        "platform": platform.system().lower()[:64],
        "agent_version": APP_VERSION,
    }


def _parse_user(payload: dict[str, Any], contract: ApiContract) -> AuthenticatedUser:
    """Build the domain identity from the server's user envelope.

    The envelope key for each field comes from the contract, so a backend that
    calls it ``team`` instead of ``workspace`` is a one-line configuration
    change rather than a patch to this module.
    """
    user = payload.get("user")
    if not isinstance(user, dict):
        raise AuthenticationError("backend returned an unexpected login response")

    def field(name: str) -> Any:
        return user.get(contract.user_key(name))

    email = field("email")
    external_user_id = field("external_user_id")
    if not isinstance(email, str) or not isinstance(external_user_id, str):
        raise AuthenticationError("backend returned an unexpected login response")

    display_name = field("display_name")
    workspace_name = field("workspace_name")

    return AuthenticatedUser(
        external_user_id=external_user_id,
        email=email,
        display_name=display_name if isinstance(display_name, str) else None,
        workspace_name=workspace_name if isinstance(workspace_name, str) else None,
    )


class ApiAuthProvider(AuthProvider):
    """Authenticate against the contract's auth endpoints."""

    def __init__(
        self,
        *,
        client: ApiHttpClient,
        token_holder: AuthTokenHolder,
        contract: ApiContract = DEFAULT_CONTRACT,
    ) -> None:
        self._client = client
        self._token_holder = token_holder
        self._contract = contract

    def login(self, email: str, password: str) -> AuthSession:
        endpoint = self._contract.login
        body = endpoint.body(
            {"email": email, "password": password, **_device_fingerprint()},
            client_id=0,
        )

        try:
            response = self._client.request(endpoint.method, endpoint.path, json=body)
        except ApiAuthError as exc:
            # Wrong credentials arrive as 422; 401 means the token store has
            # gone stale. Both are the same thing to the employee.
            raise AuthenticationError("invalid email or password") from exc
        except ApiError as exc:
            raise AuthenticationError(
                "could not reach the monitoring server; check your connection "
                "and try again"
            ) from exc

        payload = self._client.json(response, context="login")
        token = payload.get("token")
        if not isinstance(token, str) or not token:
            raise AuthenticationError("backend returned an unexpected login response")

        self._token_holder.set(token)
        _logger.info("authenticated against the monitoring backend (email=%s)", email)
        return AuthSession(user=_parse_user(payload, self._contract), token=token)

    def refresh(self, token: str) -> AuthSession:
        """Validate a persisted token at startup and extend its lifetime.

        The backend returns the same token it was given (the client keeps one
        long-lived credential in the keyring), so the only thing that changes
        here is the expiry.
        """
        endpoint = self._contract.refresh

        try:
            response = self._client.request(endpoint.method, endpoint.path, token=token)
        except ApiAuthError as exc:
            raise AuthenticationError("stored session is no longer valid") from exc
        except ApiError as exc:
            raise AuthenticationError(
                "could not reach the monitoring server; sign in again when it is reachable"
            ) from exc

        payload = self._client.json(response, context="refresh")
        self._token_holder.set(token)
        return AuthSession(user=_parse_user(payload, self._contract), token=token)

    def logout(self, token: str) -> None:
        """Best-effort revocation.

        The desktop app must always be able to log out, even with the backend
        down — the token is discarded locally either way, so a failure here is
        logged and swallowed rather than surfaced.
        """
        endpoint = self._contract.logout
        try:
            self._client.request(endpoint.method, endpoint.path, token=token)
        except ApiError as exc:
            _logger.warning("logout revocation failed (best-effort): %s", exc)
        finally:
            self._token_holder.clear()


class ApiWorkspaceProvider(WorkspaceProvider):
    """Read the workspace name the app should label itself with.

    Why this exists as its own provider: the employee renames their workspace
    from the dashboard, and a desktop app that only learned the name at login
    would keep showing the old one all day. Polling one cheap endpoint on the
    sync tick keeps the label current without a protocol change or a restart.
    """

    def __init__(
        self,
        *,
        client: ApiHttpClient,
        token_holder: AuthTokenHolder,
        contract: ApiContract = DEFAULT_CONTRACT,
    ) -> None:
        self._client = client
        self._token_holder = token_holder
        self._contract = contract

    def fetch_workspace(self) -> AuthenticatedUser | None:
        """Return the current identity, or None when it cannot be read.

        Failure is silent by design: a stale workspace label is a cosmetic
        problem, and it must never interrupt a sync drain or surface an error
        the employee can do nothing about.
        """
        token = self._token_holder.get()
        if token is None:
            return None

        endpoint = self._contract.workspace
        try:
            response = self._client.request(
                endpoint.method, endpoint.url(client_id=0), token=token
            )
            payload = self._client.json(response, context="workspace")
        except ApiError as exc:
            _logger.debug("workspace name unavailable: %s", exc)
            return None

        # The endpoint may nest the user under `user` or `workspace`; accept
        # either so a contract need not restate the envelope shape.
        envelope = payload.get("workspace")
        if not isinstance(envelope, dict):
            envelope = payload if _looks_like_user(payload) else None
        if envelope is None:
            _logger.debug("workspace response carried no user envelope")
            return None

        try:
            return _parse_user({"user": envelope}, self._contract)
        except AuthenticationError as exc:
            _logger.debug("workspace response was not a user envelope: %s", exc)
            return None


def _looks_like_user(payload: dict[str, Any]) -> bool:
    return isinstance(payload.get("email"), str) and isinstance(
        payload.get("external_user_id"), str
    )


__all__ = ["ApiAuthProvider", "ApiWorkspaceProvider"]
