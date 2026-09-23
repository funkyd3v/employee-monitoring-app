"""Unit tests for retry backoff (docs/ENGINEERING_RULES.md §Retry strategy)
and sync enums."""

from __future__ import annotations

import pytest
from app.domain.sync.sync import (
    SyncStatus,
    backoff_for_attempt,
)


@pytest.mark.parametrize(
    ("attempt", "expected_seconds"),
    [
        (1, 0),  # immediate
        (2, 30),
        (3, 120),
        (4, 300),
        (5, 900),  # cap
        (6, 900),
        (100, 900),
    ],
)
def test_backoff_schedule(attempt: int, expected_seconds: int) -> None:
    assert backoff_for_attempt(attempt) == expected_seconds


def test_backoff_rejects_zero_attempts() -> None:
    with pytest.raises(ValueError, match="attempt must be >= 1"):
        backoff_for_attempt(0)
    with pytest.raises(ValueError, match="attempt must be >= 1"):
        backoff_for_attempt(-1)


def test_sync_status_enum_values() -> None:
    assert SyncStatus.PENDING.value == "PENDING"
    assert SyncStatus.SYNCING.value == "SYNCING"
    assert SyncStatus.SYNCED.value == "SYNCED"
    assert SyncStatus.FAILED.value == "FAILED"
