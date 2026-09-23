"""Sync domain: push state and retry backoff.

Data deletion rule (docs/ENGINEERING_RULES.md §Data Deletion Rule): local
data is deleted only after the server confirms persistence. This module
models the *transition* (PENDING → SYNCING → SYNCED) and the backoff policy;
deletion of local data is a separate, gated decision owned by the sync
service, never implicit here.
"""

from __future__ import annotations

from enum import StrEnum

from app.config.constants import RETRY_BACKOFF_SECONDS


class SyncStatus(StrEnum):
    """Persisted status of an outbox item (docs/DATA_MODEL.md)."""

    PENDING = "PENDING"
    SYNCING = "SYNCING"
    SYNCED = "SYNCED"
    FAILED = "FAILED"


class SyncOperation(StrEnum):
    """Outbox operation an item represents."""

    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"


class SyncEntityType(StrEnum):
    """What an outbox item refers to (docs/DATA_MODEL.md ``sync_queue``)."""

    USER = "user"
    WORK_SESSION = "work_session"
    BREAK = "break"
    ACTIVITY_PERIOD = "activity_period"
    SCREENSHOT = "screenshot"


def backoff_for_attempt(attempt: int) -> int:
    """Delay in seconds before retrying ``attempt``.

    Table from docs §Retry strategy: 1→immediate, 2→30s, 3→2min, 4→5min,
    5+→15 min (cap). The schedule is positional so the cap is automatically
    the largest value.
    """
    if attempt <= 0:
        raise ValueError("attempt must be >= 1")
    capped = min(attempt, len(RETRY_BACKOFF_SECONDS))
    return RETRY_BACKOFF_SECONDS[capped - 1]
