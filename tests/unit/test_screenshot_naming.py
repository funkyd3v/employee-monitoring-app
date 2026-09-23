"""Unit tests for screenshot filename generation
(docs/DATA_MODEL.md § Screenshot file naming)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.domain.screenshots.naming import build_screenshot_filename

CAPTURED = datetime(2026, 9, 23, 10, 30, 0, tzinfo=UTC)


def test_filename_matches_documented_shape() -> None:
    name = build_screenshot_filename(
        session_id=7, captured_at=CAPTURED, unique_id="8f2a"
    )
    assert name == "sess_000007_20260923T103000_8f2a.jpg"


def test_filename_is_collision_resistant_via_unique_id() -> None:
    a = build_screenshot_filename(7, CAPTURED, "8f2a")
    b = build_screenshot_filename(7, CAPTURED, "9c4d")
    assert a != b


def test_filename_includes_session_and_sorts_chronologically() -> None:
    earlier = build_screenshot_filename(3, CAPTURED, "x1")
    later = build_screenshot_filename(3, CAPTURED.replace(minute=31), "y2")
    assert earlier < later


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        build_screenshot_filename(1, datetime(2026, 9, 23, 10, 30), "z")
