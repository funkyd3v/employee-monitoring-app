"""Sync provider contract + connectivity model.

Backend-readiness (docs/ARCHITECTURE.md §Backend-readiness): UI and services
depend on :class:`SyncProvider`; only the container chooses between the
local dummy (today) and a future :class:`ApiSyncProvider`.

Connectivity model (docs/ENGINEERING_RULES.md §Connectivity model): network
interface up ≠ backend reachable. Sync service checks real reachability via
the provider — the dummy simulates every state for testability.

Data deletion rule (docs/ENGINEERING_RULES.md §Data Deletion Rule): local
data is deleted only after the server confirms persistence. :class:`SyncResult`
is the gated confirmation signal.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ConnectivityState(StrEnum):
    """Discrete network/backend state tracked by the sync service."""

    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    SYNCING = "SYNCING"


@dataclass(frozen=True)
class SyncResult:
    """Outcome of one provider sync attempt (screenshot or queue item)."""

    success: bool
    retryable: bool = True
    error: str | None = None
    connectivity: ConnectivityState = ConnectivityState.ONLINE

    @classmethod
    def ok(cls) -> SyncResult:
        return cls(success=True, retryable=False, connectivity=ConnectivityState.ONLINE)

    @classmethod
    def failed(
        cls,
        message: str,
        *,
        retryable: bool = True,
        connectivity: ConnectivityState = ConnectivityState.BACKEND_UNAVAILABLE,
    ) -> SyncResult:
        return cls(
            success=False,
            retryable=retryable,
            error=message,
            connectivity=connectivity,
        )

    @classmethod
    def auth_required(cls, message: str = "authentication required") -> SyncResult:
        return cls(
            success=False,
            retryable=False,
            error=message,
            connectivity=ConnectivityState.AUTHENTICATION_REQUIRED,
        )

    @classmethod
    def offline(cls, message: str = "offline") -> SyncResult:
        return cls(
            success=False,
            retryable=True,
            error=message,
            connectivity=ConnectivityState.OFFLINE,
        )


class SyncProvider(ABC):
    """Contract every sync backend implements (dummy today, API later)."""

    @abstractmethod
    def check_connectivity(self) -> ConnectivityState:
        """Return current backend reachability (not just network interface)."""

    @abstractmethod
    def sync_item(
        self,
        *,
        entity_type: str,
        entity_id: int,
        operation: str,
        payload: dict[str, Any],
    ) -> SyncResult:
        """Sync one outbox item's JSON payload. Must be side-effect free on failure."""

    @abstractmethod
    def upload_screenshot(
        self,
        *,
        screenshot_id: int,
        file_path: str,
        metadata: dict[str, Any],
    ) -> SyncResult:
        """Upload one screenshot file + metadata. Must not delete local data."""

    def sync_batch(
        self,
        items: list[dict[str, Any]],
    ) -> list[SyncResult]:
        """Optional batch path — default fans out to :meth:`sync_item`."""
        return [
            self.sync_item(
                entity_type=str(item.get("entity_type", "")),
                entity_id=int(item.get("entity_id", 0)),
                operation=str(item.get("operation", "")),
                payload=dict(item.get("payload", {})),
            )
            for item in items
        ]


__all__ = ["ConnectivityState", "SyncProvider", "SyncResult"]
