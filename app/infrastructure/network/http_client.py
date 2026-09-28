"""HTTP plumbing for the API providers — the connectivity model made concrete.

``docs/ENGINEERING_RULES.md`` §Connectivity model is the contract this module
implements: "network interface up ≠ backend reachable", so the agent tracks
five distinct states and must be able to tell them apart from the wire alone.
That means every transport outcome has exactly one classification, decided
here once instead of in each provider:

===========================  ==========================  ==================
Outcome                      Exception                  ConnectivityState
===========================  ==========================  ==================
DNS / connect / TLS /        :class:`ApiUnavailableError` BACKEND_UNAVAILABLE
read timeout
401 / 403 (token)            :class:`ApiAuthError`       AUTHENTICATION_REQUIRED
5xx, 429, 408                :class:`ApiTransientError`  BACKEND_UNAVAILABLE
422 / 400 / 404 / 409        :class:`ApiRejectedError`   (unchanged, offline)
malformed JSON               :class:`ApiProtocolError`   BACKEND_UNAVAILABLE
===========================  ==========================  ==================

Non-retryable classifications are the important half. A rejected payload will
never succeed on replay, and retrying it forever is how a queue turns into a
hot loop against a backend that is working perfectly.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, Final

import httpx

from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.domain.sync.provider import ConnectivityState, SyncResult

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

_logger = get_logger("api.client")

DEFAULT_USER_AGENT: Final = "employee-monitoring-agent/0.1"

# Status codes that mean "the server is up but could not answer this"; the
# request is worth repeating unchanged.
_TRANSIENT_STATUSES: Final = frozenset({408, 425, 429, 500, 502, 503, 504})


class ApiError(AppError):
    """Base for every classified API failure."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code

    @property
    def connectivity(self) -> ConnectivityState:
        raise NotImplementedError

    @property
    def retryable(self) -> bool:
        raise NotImplementedError

    def to_sync_result(self) -> SyncResult:
        """Project this failure onto the sync service's result type."""
        return SyncResult(
            success=False,
            retryable=self.retryable,
            error=str(self),
            connectivity=self.connectivity,
        )


class ApiUnavailableError(ApiError):
    """The backend could not be reached, or answered in a way that is worth
    retrying unchanged (5xx, rate limit, timeout)."""

    @property
    def connectivity(self) -> ConnectivityState:
        return ConnectivityState.BACKEND_UNAVAILABLE

    @property
    def retryable(self) -> bool:
        return True


class ApiAuthError(ApiError):
    """The token is missing, expired or rejected. Retrying cannot help."""

    @property
    def connectivity(self) -> ConnectivityState:
        return ConnectivityState.AUTHENTICATION_REQUIRED

    @property
    def retryable(self) -> bool:
        return False


class ApiRejectedError(ApiError):
    """The server understood the request and refused it (4xx).

    A replayed identical request would be refused identically, so this is
    reported as non-retryable to stop the outbox spinning. The local data is
    still kept — only the confirmation never arrives.
    """

    @property
    def connectivity(self) -> ConnectivityState:
        return ConnectivityState.ONLINE

    @property
    def retryable(self) -> bool:
        return False


class ApiProtocolError(ApiError):
    """A 2xx response whose body is not the documented shape.

    Treated as unavailable: it is almost always a proxy, a captive portal or
    a wrong ``api_base_url``, all of which may resolve on their own.
    """

    @property
    def connectivity(self) -> ConnectivityState:
        return ConnectivityState.BACKEND_UNAVAILABLE

    @property
    def retryable(self) -> bool:
        return True


def classify_response(response: httpx.Response) -> ApiError | None:
    """Return the failure an HTTP response represents, or None if it is fine."""
    if response.is_success:
        return None
    if response.status_code in (401, 403):
        return ApiAuthError(
            f"backend rejected the agent token (HTTP {response.status_code})",
            status_code=response.status_code,
        )
    if response.status_code in _TRANSIENT_STATUSES:
        return ApiUnavailableError(
            f"backend temporarily unavailable (HTTP {response.status_code})",
            status_code=response.status_code,
        )
    return ApiRejectedError(
        f"backend rejected the request (HTTP {response.status_code})",
        status_code=response.status_code,
    )


def _body_summary(response: httpx.Response) -> str:
    """Short, non-sensitive description of an error body for the log."""
    try:
        payload = response.json()
    except ValueError:
        return response.text[:200]

    if isinstance(payload, dict):
        message = payload.get("message") or payload.get("error")
        if isinstance(message, str):
            return message[:200]
        errors = payload.get("errors")
        if isinstance(errors, dict) and errors:
            return "; ".join(
                f"{field}: {', '.join(str(item) for item in value[:3])}"
                for field, value in errors.items()
                if isinstance(value, list)
            )[:200]

    return str(payload)[:200]


class ApiHttpClient:
    """Thin ``httpx`` wrapper that owns classification and token attachment.

    One client per process, reused across threads (``httpx.Client`` is
    thread-safe and pools connections), created lazily so that a local-mode
    run never opens a socket.
    """

    def __init__(
        self,
        *,
        base_url: str,
        connect_timeout: float = 5.0,
        timeout: float = 30.0,
        user_agent: str = DEFAULT_USER_AGENT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("ApiHttpClient requires a base_url in api mode")
        self._base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=httpx.Timeout(timeout, connect=connect_timeout),
            headers={
                "Accept": "application/json",
                "User-Agent": user_agent,
            },
            follow_redirects=False,
            # Injectable so tests can drive every branch — timeout, 5xx, 422,
            # connection refused — against a real ``httpx`` code path.
            transport=transport,
        )
        self._closed = False
        self._lock = threading.Lock()

    @property
    def base_url(self) -> str:
        return self._base_url

    def request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        json: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        files: Any = None,
    ) -> httpx.Response:
        """Perform one request, converting any failure into a typed error.

        Raises only :class:`ApiError` subclasses, so callers never need to
        know about ``httpx`` exception types.
        """
        headers = {"Authorization": f"Bearer {token}"} if token else None

        try:
            response = self._client.request(
                method,
                path,
                json=json,
                data=data,
                files=files,
                headers=headers,
            )
        except httpx.TimeoutException as exc:
            raise ApiUnavailableError(f"backend timed out: {exc}") from exc
        except httpx.TransportError as exc:
            # DNS failure, connection refused, TLS handshake failure, reset —
            # all "not reachable right now", never "your data is bad".
            raise ApiUnavailableError(f"backend unreachable: {exc}") from exc

        failure = classify_response(response)
        if failure is not None:
            _logger.warning(
                "api %s %s failed: %s", method, path, _body_summary(response)
            )
            raise failure

        return response

    def json(self, response: httpx.Response, *, context: str) -> dict[str, Any]:
        """Decode a success body, or raise :class:`ApiProtocolError`."""
        try:
            payload = response.json()
        except ValueError as exc:
            raise ApiProtocolError(
                f"{context}: backend returned a non-JSON response"
            ) from exc

        if not isinstance(payload, dict):
            raise ApiProtocolError(f"{context}: backend returned an unexpected body")

        return payload

    def is_reachable(self, *, timeout: float | None = None) -> bool:
        """Unauthenticated liveness probe used by the connectivity check."""
        try:
            response = self._client.get("/health", timeout=timeout)
        except httpx.HTTPError:
            return False
        return response.is_success

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._client.close()

    def upload_file(
        self,
        path: str,
        file_path: Path,
        *,
        token: str,
        data: Mapping[str, Any],
        field: str = "file",
        content_type: str | None = None,
    ) -> None:
        """Stream a screenshot as multipart, without buffering it in memory.

        The field name and content type come from the contract, so a backend
        that names its part differently is a configuration change rather than
        an edit here.

        Raises the same typed errors as :meth:`request` — an upload that comes
        back 503 is as retryable as any other transient failure, and treating
        it differently would strand screenshots in the outbox forever.
        """
        try:
            with file_path.open("rb") as handle:
                response = self._client.request(
                    "POST",
                    path,
                    files={
                        field: (file_path.name, handle, content_type or "image/jpeg")
                    },
                    data=dict(data),
                    headers={
                        "Accept": "application/json",
                        "Authorization": f"Bearer {token}",
                    },
                )
        except httpx.TimeoutException as exc:
            raise ApiUnavailableError(
                f"backend timed out during upload: {exc}"
            ) from exc
        except httpx.TransportError as exc:
            raise ApiUnavailableError(
                f"backend unreachable during upload: {exc}"
            ) from exc
        except OSError as exc:
            raise ApiRejectedError(f"screenshot file could not be read: {exc}") from exc

        failure = classify_response(response)
        if failure is not None:
            _logger.warning(
                "screenshot upload to %s failed: %s", path, _body_summary(response)
            )
            raise failure


__all__ = [
    "ApiAuthError",
    "ApiError",
    "ApiHttpClient",
    "ApiProtocolError",
    "ApiRejectedError",
    "ApiUnavailableError",
    "classify_response",
]
