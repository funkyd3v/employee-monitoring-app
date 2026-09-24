"""Contract-first JSON payload shapes for future API.

These shapes are what a realistic REST backend would expect
(docs/ARCHITECTURE.md §Backend-readiness). The dummy provider already
honors them so the eventual contract is "implement what the client sends".

Illustrative future endpoints the payloads map to:
  POST /work-sessions
  PATCH /work-sessions/{id}
  POST /breaks
  POST /activities/batch
  POST /screenshots
  POST /sync/batch
  GET /agent/config
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def _iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("payload timestamps must be timezone-aware (UTC)")
    return value.astimezone(UTC).isoformat()


def build_user_payload(
    *,
    external_user_id: str | None,
    email: str,
    display_name: str | None,
    team_name: str | None,
) -> dict[str, Any]:
    return {
        "external_user_id": external_user_id,
        "email": email,
        "display_name": display_name,
        "team_name": team_name,
    }


def build_work_session_payload(
    *,
    id: int,
    user_id: int,
    started_at: datetime,
    ended_at: datetime | None,
    status: str,
    total_work_seconds: int,
) -> dict[str, Any]:
    return {
        "id": id,
        "user_id": user_id,
        "started_at": _iso_utc(started_at),
        "ended_at": _iso_utc(ended_at),
        "status": status,
        "total_work_seconds": total_work_seconds,
    }


def build_break_payload(
    *,
    id: int,
    session_id: int,
    started_at: datetime,
    ended_at: datetime | None,
    duration_seconds: int,
) -> dict[str, Any]:
    return {
        "id": id,
        "session_id": session_id,
        "started_at": _iso_utc(started_at),
        "ended_at": _iso_utc(ended_at),
        "duration_seconds": duration_seconds,
    }


def build_activity_period_payload(
    *,
    id: int,
    session_id: int,
    started_at: datetime,
    ended_at: datetime | None,
    state: str,
    duration_seconds: int,
) -> dict[str, Any]:
    return {
        "id": id,
        "session_id": session_id,
        "started_at": _iso_utc(started_at),
        "ended_at": _iso_utc(ended_at),
        "state": state,
        "duration_seconds": duration_seconds,
    }


def build_screenshot_payload(
    *,
    id: int,
    session_id: int,
    captured_at: datetime,
    activity_state: str,
    file_size: int | None,
    checksum: str | None,
    sync_status: str,
) -> dict[str, Any]:
    return {
        "id": id,
        "session_id": session_id,
        "captured_at": _iso_utc(captured_at),
        "activity_state": activity_state,
        "file_size": file_size,
        "checksum": checksum,
        "sync_status": sync_status,
    }


def build_sync_queue_payload(
    *,
    id: int,
    entity_type: str,
    entity_id: int,
    operation: str,
    status: str,
    attempt_count: int,
) -> dict[str, Any]:
    """Envelope for outbox rows — payload is filled by type-specific builders."""
    return {
        "id": id,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "operation": operation,
        "status": status,
        "attempt_count": attempt_count,
    }
