"""Unit tests for the time-source module (docs/ENGINEERING_RULES.md
§Time & clock handling)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from app.core.clock import Clock, monotonic_now, utc_now


def test_utc_now_is_timezone_aware_utc() -> None:
    now = utc_now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


def test_monotonic_is_strictly_forward() -> None:
    first = monotonic_now()
    second = monotonic_now()
    assert second >= first


def test_injected_clock_returns_fixed_time() -> None:
    fixed = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
    clock = Clock(utc=lambda: fixed, monotonic=lambda: 42.0)
    assert clock.utc() == fixed
    assert clock.monotonic() == 42.0


def test_conversion_to_utc_preserves_moment() -> None:
    tz_east = timezone(timedelta(hours=5))
    local = datetime(2026, 9, 23, 15, 0, tzinfo=tz_east)
    converted = local.astimezone(UTC)
    assert converted.hour == 10
    assert converted.utcoffset() == timedelta(0)
