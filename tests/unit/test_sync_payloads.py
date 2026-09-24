"""Tests for contract-first JSON payload builders."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.domain.sync.payloads import (
    build_activity_period_payload,
    build_break_payload,
    build_screenshot_payload,
    build_work_session_payload,
)

T0 = datetime(2026, 9, 23, 10, 0, tzinfo=UTC)
T1 = datetime(2026, 9, 23, 11, 0, tzinfo=UTC)


def test_work_session_payload_iso() -> None:
    payload = build_work_session_payload(id=1, user_id=2, started_at=T0, ended_at=T1, status="WORKING", total_work_seconds=3600)
    assert payload["started_at"] == T0.isoformat()
    assert payload["ended_at"] == T1.isoformat()
    assert payload["id"] == 1
    assert payload["status"] == "WORKING"


def test_break_payload_none_ended() -> None:
    payload = build_break_payload(id=5, session_id=1, started_at=T0, ended_at=None, duration_seconds=0)
    assert payload["ended_at"] is None
    assert payload["duration_seconds"] == 0


def test_activity_payload() -> None:
    payload = build_activity_period_payload(id=10, session_id=1, started_at=T0, ended_at=T1, state="ACTIVE", duration_seconds=3600)
    assert payload["state"] == "ACTIVE"


def test_screenshot_payload() -> None:
    payload = build_screenshot_payload(id=7, session_id=1, captured_at=T0, activity_state="IDLE", file_size=1234, checksum="abc", sync_status="PENDING")
    assert payload["captured_at"] == T0.isoformat()
    assert payload["activity_state"] == "IDLE"


def test_payload_rejects_naive_datetime() -> None:
    naive = datetime(2026, 9, 23, 10, 0)  # no tz
    with pytest.raises(ValueError, match="timezone-aware"):
        build_work_session_payload(id=1, user_id=1, started_at=naive, ended_at=None, status="WORKING", total_work_seconds=0)
