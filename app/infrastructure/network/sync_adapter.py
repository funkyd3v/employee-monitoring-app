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
from typing import Any

from app.core.logging import get_logger
from app.domain.sync.provider import ConnectivityState, SyncProvider, SyncResult

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
    """Future HTTP-backed provider — stub that enforces api-mode wiring.

    Not implemented in this phase (docs/ARCHITECTURE.md §Explicitly out of
    scope). Exists so the container can select ``mode: \"api\"`` and fail
    with a clear error rather than silently falling back to dummy.
    """

    def __init__(self, *, base_url: str = "") -> None:
        self._base_url = base_url

    def check_connectivity(self) -> ConnectivityState:
        _logger.warning(
            "ApiSyncProvider not implemented — treating as BACKEND_UNAVAILABLE"
        )
        return ConnectivityState.BACKEND_UNAVAILABLE

    def sync_item(
        self,
        *,
        entity_type: str,  # noqa: ARG002
        entity_id: int,  # noqa: ARG002
        operation: str,  # noqa: ARG002
        payload: dict[str, Any],  # noqa: ARG002
    ) -> SyncResult:
        return SyncResult.failed(
            "ApiSyncProvider not implemented (phase 10+)",
            retryable=False,
            connectivity=ConnectivityState.BACKEND_UNAVAILABLE,
        )

    def upload_screenshot(
        self,
        *,
        screenshot_id: int,  # noqa: ARG002
        file_path: str,  # noqa: ARG002
        metadata: dict[str, Any],  # noqa: ARG002
    ) -> SyncResult:
        return SyncResult.failed(
            "ApiSyncProvider not implemented (phase 10+)",
            retryable=False,
            connectivity=ConnectivityState.BACKEND_UNAVAILABLE,
        )


__all__ = ["ApiSyncProvider", "DummySyncProvider"]
