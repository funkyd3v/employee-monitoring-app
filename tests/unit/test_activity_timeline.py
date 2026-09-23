"""Unit tests for activity timeline derivation (docs/ENGINEERING_RULES.md
§Sleep/lock handling, §Timer Correctness)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.domain.activity.activity import (
    ACTIVE_RESUMED,
    IDLE_CONTINUED,
    IDLE_STARTED,
    INPUT_ACTIVITY_DETECTED,
    ActivityEvent,
    ActivityState,
    close_open_period,
    derive_timeline,
)

T0 = datetime(2026, 9, 23, 10, 0, tzinfo=UTC)
THRESHOLD = timedelta(seconds=60)


def ev_at(seconds: int, name: str = INPUT_ACTIVITY_DETECTED) -> ActivityEvent:
    return ActivityEvent(name=name, happened_at=T0 + timedelta(seconds=seconds))


def test_empty_stream_yields_no_periods() -> None:
    assert derive_timeline([], THRESHOLD) == []


def test_single_event_keeps_an_open_active_period() -> None:
    timeline = derive_timeline([ev_at(0)], THRESHOLD)
    assert len(timeline) == 1
    assert timeline[0].state is ActivityState.ACTIVE
    assert timeline[0].is_open


def test_activity_within_threshold_stays_active() -> None:
    timeline = derive_timeline([ev_at(0), ev_at(30)], THRESHOLD)
    assert len(timeline) == 1
    assert timeline[0].state is ActivityState.ACTIVE


def test_quiet_gap_crosses_threshold_creating_idle_period() -> None:
    timeline = derive_timeline([ev_at(0), ev_at(120)], THRESHOLD)
    assert len(timeline) == 3
    active0, idle, active1 = timeline
    assert active0.state is ActivityState.ACTIVE
    assert active0.ended_at == T0 + THRESHOLD
    assert idle.state is ActivityState.IDLE
    assert idle.started_at == T0 + THRESHOLD
    assert idle.ended_at == T0 + timedelta(seconds=120)
    assert active1.state is ActivityState.ACTIVE
    assert active1.is_open


def test_resume_event_after_idle_reopens_activity() -> None:
    timeline = derive_timeline([ev_at(0), ev_at(200, name=ACTIVE_RESUMED)], THRESHOLD)
    assert any(p.state is ActivityState.IDLE for p in timeline)
    assert timeline[-1].state is ActivityState.ACTIVE


def test_idle_info_events_do_not_create_periods() -> None:
    timeline = derive_timeline([ev_at(0), ev_at(90, IDLE_STARTED)], THRESHOLD)
    assert all(p.state is ActivityState.ACTIVE for p in timeline)


def test_threshold_must_be_positive() -> None:
    with pytest.raises(ValueError, match="idle threshold must be positive"):
        derive_timeline([ev_at(0)], timedelta(seconds=0))


def test_disallowed_event_name_rejected() -> None:
    with pytest.raises(ValueError, match="disallowed activity event"):
        ActivityEvent(name="TYPED_TEXT_abc", happened_at=T0)


@pytest.mark.parametrize(
    "name",
    [
        INPUT_ACTIVITY_DETECTED,
        IDLE_STARTED,
        IDLE_CONTINUED,
        ACTIVE_RESUMED,
    ],
)
def test_allowed_event_names_accepted(name: str) -> None:
    assert ActivityEvent(name=name, happened_at=T0).name == name


def test_close_open_period_extends_activity_when_within_threshold() -> None:
    timeline = derive_timeline([ev_at(0), ev_at(30)], THRESHOLD)
    closed = close_open_period(
        timeline,
        active_started_at=T0,
        last_activity_at=T0 + timedelta(seconds=30),
        until=T0 + timedelta(seconds=45),
        idle_threshold=THRESHOLD,
    )
    assert len(closed) == 1
    assert closed[0].ended_at == T0 + timedelta(seconds=45)


def test_close_open_period_adds_trailing_idle_when_quiet() -> None:
    timeline = derive_timeline([ev_at(0)], THRESHOLD)
    closed = close_open_period(
        timeline,
        active_started_at=T0,
        last_activity_at=T0,
        until=T0 + timedelta(seconds=180),
        idle_threshold=THRESHOLD,
    )
    assert len(closed) == 2
    assert closed[1].state is ActivityState.IDLE
    assert closed[1].ended_at == T0 + timedelta(seconds=180)
