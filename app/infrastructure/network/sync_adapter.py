"""Sync provider adapters — dummy today, API later.

Backend-readiness (docs/ARCHITECTURE.md §Backend-readiness): services depend
on :class:`app.domain.sync.provider.SyncProvider`; only the container chooses
which implementation is active. This file owns concrete adapters.

Data deletion rule: adapter never deletes local files/rows — it only returns
:class:`SyncResult`. Deletion is owned by :class:`SyncService`.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger
from app.domain.sync.provider import ConnectivityState, SyncProvider, SyncResult
from app.infrastructure.network.contract import DEFAULT_CONTRACT, ApiContract, Endpoint
from app.infrastructure.network.http_client import ApiAuthError, ApiError, ApiHttpClient

if TYPE_CHECKING:
    import httpx

    from app.domain.auth.token_holder import AuthTokenHolder

_logger = get_logger("sync.provider")


class DummySyncProvider(SyncProvider):
    """Local dummy that simulates every connectivity state without a backend.

    Designed for testability (docs/ENGINEERING_RULES.md §Connectivity model
    and §Retry strategy): callers can flip connectivity, inject failures, and
    assert that offline data is preserved and retried.

    Thread-safe: ``set_*`` helpers may be called from tests or UI while
    workers call ``sync_item`` concurrently.
    """

    def __init__(
        self,
        *,
        connectivity: ConnectivityState = ConnectivityState.ONLINE,
        fail_next: int = 0,
        always_fail: bool = False,
        latency_ms: int = 0,
    ) -> None:
        self._connectivity = connectivity
        self._fail_next = fail_next
        self._always_fail = always_fail
        self._latency_ms = latency_ms
        self._lock = threading.RLock()
        self._uploaded_screenshots: list[dict[str, Any]] = []
        self._synced_items: list[dict[str, Any]] = []
        self._call_count = 0

    # ── Test helpers ────────────────────────────────────────────────────

    def set_connectivity(self, state: ConnectivityState) -> None:
        with self._lock:
            self._connectivity = state

    def set_fail_next(self, count: int) -> None:
        with self._lock:
            self._fail_next = count

    def set_always_fail(self, value: bool) -> None:
        with self._lock:
            self._always_fail = value

    @property
    def synced_items(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._synced_items)

    @property
    def uploaded_screenshots(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._uploaded_screenshots)

    @property
    def call_count(self) -> int:
        with self._lock:
            return self._call_count

    def reset(self) -> None:
        with self._lock:
            self._uploaded_screenshots.clear()
            self._synced_items.clear()
            self._call_count = 0
            self._fail_next = 0
            self._always_fail = False
            self._connectivity = ConnectivityState.ONLINE

    # ── SyncProvider ───────────────────────────────────────────────────

    def check_connectivity(self) -> ConnectivityState:
        with self._lock:
            return self._connectivity

    def sync_item(
        self,
        *,
        entity_type: str,
        entity_id: int,
        operation: str,
        payload: dict[str, Any],
    ) -> SyncResult:
        with self._lock:
            self._call_count += 1
            connectivity = self._connectivity

            if connectivity == ConnectivityState.OFFLINE:
                return SyncResult.offline("dummy: offline")

            if connectivity == ConnectivityState.AUTHENTICATION_REQUIRED:
                return SyncResult.auth_required("dummy: auth required")

            if connectivity == ConnectivityState.BACKEND_UNAVAILABLE:
                return SyncResult.failed(
                    "dummy: backend unavailable",
                    retryable=True,
                    connectivity=connectivity,
                )

            if self._always_fail:
                return SyncResult.failed(
                    "dummy: injected failure",
                    retryable=True,
                    connectivity=connectivity,
                )

            if self._fail_next > 0:
                self._fail_next -= 1
                return SyncResult.failed(
                    "dummy: injected transient failure",
                    retryable=True,
                    connectivity=connectivity,
                )

            if self._latency_ms:
                time.sleep(self._latency_ms / 1000.0)

            self._synced_items.append(
                {
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "operation": operation,
                    "payload": dict(payload),
                }
            )
            _logger.debug(
                "dummy synced %s:%s op=%s",
                entity_type,
                entity_id,
                operation,
            )
            return SyncResult.ok()

    def upload_screenshot(
        self,
        *,
        screenshot_id: int,
        file_path: str,
        metadata: dict[str, Any],
    ) -> SyncResult:
        with self._lock:
            self._call_count += 1
            connectivity = self._connectivity

            if connectivity == ConnectivityState.OFFLINE:
                return SyncResult.offline("dummy: offline (screenshot)")

            if connectivity == ConnectivityState.AUTHENTICATION_REQUIRED:
                return SyncResult.auth_required("dummy: auth required (screenshot)")

            if connectivity == ConnectivityState.BACKEND_UNAVAILABLE:
                return SyncResult.failed(
                    "dummy: backend unavailable (screenshot)",
                    retryable=True,
                    connectivity=connectivity,
                )

            if self._always_fail:
                return SyncResult.failed(
                    "dummy: injected failure (screenshot)",
                    retryable=True,
                    connectivity=connectivity,
                )

            if self._fail_next > 0:
                self._fail_next -= 1
                return SyncResult.failed(
                    "dummy: injected transient failure (screenshot)",
                    retryable=True,
                    connectivity=connectivity,
                )

            # Validate file exists and checksum if provided — mirrors real backend
            # validation without ever deleting local data.
            path = Path(file_path)
            if not path.exists():
                return SyncResult.failed(
                    f"screenshot file missing: {file_path}",
                    retryable=False,
                    connectivity=connectivity,
                )
            if not path.is_file():
                return SyncResult.failed(
                    f"screenshot path not a file: {file_path}",
                    retryable=False,
                    connectivity=connectivity,
                )
            try:
                size = path.stat().st_size
                if size == 0:
                    return SyncResult.failed(
                        "screenshot file empty",
                        retryable=False,
                        connectivity=connectivity,
                    )
            except OSError as exc:
                return SyncResult.failed(
                    f"screenshot file unreadable: {exc}",
                    retryable=True,
                    connectivity=connectivity,
                )

            if self._latency_ms:
                time.sleep(self._latency_ms / 1000.0)

            self._uploaded_screenshots.append(
                {
                    "screenshot_id": screenshot_id,
                    "file_path": file_path,
                    "metadata": dict(metadata),
                    "file_size": size,
                }
            )
            self._synced_items.append(
                {
                    "entity_type": "screenshot",
                    "entity_id": screenshot_id,
                    "operation": "CREATE",
                    "metadata": dict(metadata),
                }
            )
            _logger.debug("dummy uploaded screenshot id=%s", screenshot_id)
            return SyncResult.ok()


class ApiSyncProvider(SyncProvider):
    """HTTP-backed provider for a real monitoring backend.

    Backend-readiness (docs/ARCHITECTURE.md §Backend-readiness): the container
    selects this implementation when ``mode: "api"``; services keep depending
    on :class:`SyncProvider` only.

    Design rules that follow from the client's own invariants:

    * **One item per request.** The outbox may only delete a local row after
      an unambiguous confirmation, so every call sends exactly one entity and
      the HTTP status is that item's whole answer. No batching.
    * **Confirm-then-delete.** This provider never touches local rows or
      files — :class:`~app.services.sync_service.SyncService` owns deletion and
      acts only on :attr:`SyncResult.success`.
    * **The token is read, not owned.** It comes from the injected
      :class:`AuthTokenHolder`, so this class never imports an auth service
      and the sync layer stays ignorant of how login happened.
    * **The wire shape is data, not code.** Every path, field list and
      multipart form comes from the injected :class:`ApiContract`, so this
      class contains no per-entity branch: a backend that starts accepting a
      new entity syncs because the contract declares it, not because this file
      was edited.
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

    # ── SyncProvider ───────────────────────────────────────────────────

    def check_connectivity(self) -> ConnectivityState:
        """Probe the backend, then the credential, per the connectivity model.

        Two distinct questions, never conflated: *is the backend there* and
        *is our token still good*. A reachable server with a dead token is
        AUTHENTICATION_REQUIRED, not ONLINE — the outbox must not spin
        against a server that will reject every item.
        """
        if not self._client.is_reachable(timeout=self._probe_timeout):
            _logger.debug("connectivity probe failed — backend unreachable")
            return ConnectivityState.BACKEND_UNAVAILABLE

        token = self._token_holder.get()
        if token is None:
            return ConnectivityState.AUTHENTICATION_REQUIRED

        identity = self._contract.identity
        try:
            self._client.request(
                identity.method, identity.url(client_id=0), token=token
            )
        except ApiAuthError:
            return ConnectivityState.AUTHENTICATION_REQUIRED
        except ApiError as exc:
            return exc.connectivity

        return ConnectivityState.ONLINE

    def sync_item(
        self,
        *,
        entity_type: str,
        entity_id: int,
        operation: str,  # noqa: ARG002 — every write is a full upsert
        payload: dict[str, Any],
    ) -> SyncResult:
        """Upsert one entity, driven by the contract.

        ``operation`` is intentionally unused: CREATE and UPDATE both converge
        on the same server-side state, so a replayed CREATE and a replayed
        UPDATE are the same request. That is what makes retries safe.
        """
        endpoint = self._contract.entity(entity_type)
        if endpoint is None:
            # A type the contract does not describe. Deliberately
            # non-retryable: retrying cannot invent a route for it, and
            # spinning on it would keep the local row forever.
            return SyncResult.failed(
                f"unsupported entity type: {entity_type}",
                retryable=False,
                connectivity=ConnectivityState.ONLINE,
            )

        if not payload:
            return SyncResult.failed(
                f"payload for {entity_type} {entity_id} is empty",
                retryable=False,
                connectivity=ConnectivityState.ONLINE,
            )

        body = endpoint.body(payload, client_id=entity_id)

        try:
            response = self._request_with_token_retry(
                endpoint.method,
                endpoint.url(client_id=entity_id),
                json=body if endpoint.sends_body else None,
            )
        except ApiError as exc:
            return exc.to_sync_result()

        _logger.debug("synced %s:%s via %s", entity_type, entity_id, endpoint.name)
        return (
            SyncResult.ok()
            if response.is_success
            else SyncResult.failed(
                "backend accepted the request but did not confirm persistence",
                retryable=True,
            )
        )

    def upload_screenshot(
        self,
        *,
        screenshot_id: int,
        file_path: str,
        metadata: dict[str, Any],
    ) -> SyncResult:
        """Upload one screenshot file plus its metadata.

        The metadata is repeated in the multipart body as well as living in its
        own row, so this call is self-sufficient: a retry that arrives after
        the metadata write already succeeded still lands on the right capture.
        """
        endpoint = self._contract.file_endpoint("screenshot")
        if endpoint is None:
            return SyncResult.failed(
                "this backend contract declares no screenshot upload endpoint",
                retryable=False,
                connectivity=ConnectivityState.ONLINE,
            )

        path = Path(file_path)
        if not path.is_file():
            # Nothing to upload and nothing a retry can change; the local row
            # is left alone so the discrepancy stays visible.
            return SyncResult.failed(
                f"screenshot file missing: {file_path}",
                retryable=False,
                connectivity=ConnectivityState.ONLINE,
            )

        form = endpoint.form({"id": screenshot_id, **metadata})
        url = endpoint.url(client_id=screenshot_id)

        try:
            self._upload_with_token_retry(endpoint, url, path, form)
        except ApiError as exc:
            return exc.to_sync_result()

        _logger.info("screenshot %s uploaded", screenshot_id)
        return SyncResult.ok()

    # ── Internals ──────────────────────────────────────────────────────

    @property
    def _probe_timeout(self) -> float:
        return 5.0

    def _request_with_token_retry(
        self, method: str, path: str, *, json: dict[str, Any] | None
    ) -> httpx.Response:
        """Send a request, transparently refreshing an expired token once.

        A long-lived desktop token can expire mid-shift. Refreshing here —
        rather than failing the item and waiting for the next app start —
        keeps monitoring data flowing without ever exposing the token to the
        sync service.
        """
        token = self._token_holder.get()
        if token is None:
            raise ApiAuthError("no session token available")

        try:
            return self._client.request(method, path, token=token, json=json)
        except ApiAuthError:
            if not self._try_refresh():
                raise

        refreshed = self._token_holder.get()
        if refreshed is None:
            raise ApiAuthError("token refresh produced no credential")

        return self._client.request(method, path, token=refreshed, json=json)

    def _try_refresh(self) -> bool:
        token = self._token_holder.get()
        if token is None:
            return False

        refresh = self._contract.refresh
        try:
            self._client.request(refresh.method, refresh.path, token=token)
        except ApiError as exc:
            _logger.warning("silent token refresh failed: %s", exc)
            self._token_holder.clear()
            return False

        _logger.info("agent token refreshed")
        return True

    def _upload_with_token_retry(
        self,
        endpoint: Endpoint,
        url: str,
        path: Path,
        form: dict[str, str],
    ) -> None:
        """Upload once, silently re-authenticating and retrying exactly once.

        The same contract as :meth:`_request_with_token_retry`: a long-lived
        desktop token can expire mid-shift, and the screenshot must not be
        stranded in the outbox because of it.
        """
        token = self._token_holder.get()
        if token is None:
            raise ApiAuthError("no session token available for screenshot upload")

        try:
            self._client.upload_file(
                url,
                path,
                token=token,
                data=form,
                field=endpoint.file_field or "file",
                content_type=endpoint.file_content_type,
            )
            return
        except ApiAuthError:
            if not self._try_refresh():
                raise

        refreshed = self._token_holder.get()
        if refreshed is None:
            raise ApiAuthError("token refresh produced no credential")

        self._client.upload_file(
            url,
            path,
            token=refreshed,
            data=form,
            field=endpoint.file_field or "file",
            content_type=endpoint.file_content_type,
        )


__all__ = ["ApiSyncProvider", "DummySyncProvider"]
