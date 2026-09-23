"""Activity domain: active/idle sub-state and timeline derivation.

Privacy boundary (docs/ENGINEERING_RULES.md §Privacy boundary): the engine
records *that* input occurred, never *what* was input. Only the event names
below may ever be produced; no key values, characters, coordinates, or
clipboard content are represented here — enforced structurally by
:class:`ActivityEvent` rejecting any other name.

Idle periods are derived from the *gap* between persisted event timestamps
against the configured idle threshold. Nothing here accumulates time in
memory (docs §Timer Correctness, §Sleep/lock handling).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime, timedelta


class ActivityState(StrEnum):
    """Sub-state of a WORKING session (persisted on periods/screenshots)."""

    ACTIVE = "ACTIVE"
    IDLE = "IDLE"


# The only activity events the engine may emit (docs §Privacy boundary).
INPUT_ACTIVITY_DETECTED = "INPUT_ACTIVITY_DETECTED"
IDLE_STARTED = "IDLE_STARTED"
IDLE_CONTINUED = "IDLE_CONTINUED"
ACTIVE_RESUMED = "ACTIVE_RESUMED"

ALLOWED_EVENT_NAMES: frozenset[str] = frozenset(
    {
        INPUT_ACTIVITY_DETECTED,
        IDLE_STARTED,
        IDLE_CONTINUED,
        ACTIVE_RESUMED,
    }
)


@dataclass(frozen=True)
class ActivityEvent:
    """One activity tick: event name + observed wall time (UTC)."""

    name: str
    happened_at: datetime

    def __post_init__(self) -> None:
        if self.name not in ALLOWED_EVENT_NAMES:
            raise ValueError(f"disallowed activity event: {self.name!r}")


@dataclass
class ActivityPeriod:
    """A contiguous ACTIVE or IDLE interval within a session."""

    state: ActivityState
    started_at: datetime
    ended_at: datetime | None = None

    @property
    def is_open(self) -> bool:
        return self.ended_at is None

    @property
    def duration_seconds(self) -> int:
        end = self.ended_at or self.started_at
        return max(0, int((end - self.started_at).total_seconds()))


ActivityTimeline = list[ActivityPeriod]


def derive_timeline(
    events: list[ActivityEvent],
    idle_threshold: timedelta,
) -> ActivityTimeline:
    """Build the ACTIVE/IDLE timeline from persisted event timestamps.

    Rules (docs §Sleep/lock & §Timer Correctness):
    * The timeline is a pure projection of the event stream; a system sleep,
      NTP adjustment, or app freeze cannot distort it because gaps are
      measured between real timestamps.
    * A gap >= threshold after the last activity event starts an IDLE period
      at ``last_activity_at + threshold``; the next activity event ends it.
    * Trailing state stays open — the caller closes it at check-out / exit
      via :func:`close_open_period`.
    """
    if idle_threshold.total_seconds() <= 0:
        raise ValueError("idle threshold must be positive")

    ordered = sorted(events, key=lambda e: e.happened_at)
    timeline: ActivityTimeline = []
    last_activity_at: datetime | None = None
    active_started_at: datetime | None = None

    for event in ordered:
        ts = event.happened_at
        if event.name in (INPUT_ACTIVITY_DETECTED, ACTIVE_RESUMED):
            if active_started_at is None:
                active_started_at = ts
            elif last_activity_at is not None and ts - last_activity_at >= (
                idle_threshold
            ):
                # New activity after idle: close the trailing IDLE gap
                timeline.append(
                    ActivityPeriod(
                        ActivityState.ACTIVE,
                        active_started_at,
                        last_activity_at + idle_threshold,
                    )
                )
                timeline.append(
                    ActivityPeriod(
                        ActivityState.IDLE,
                        last_activity_at + idle_threshold,
                        ts,
                    )
                )
                active_started_at = ts
            last_activity_at = ts
        # IDLE_STARTED / IDLE_CONTINUED are informational events (emitted by
        # the worker); the timeline is derived from the timestamps themselves,
        # so a gap is authoritative — these events never *create* periods.

    # Trailing state stays open: the current ACTIVE run (or nothing, if no
    # activity was ever observed) is left open for the caller to close via
    # close_open_period at check-out / exit.
    if active_started_at is not None:
        timeline.append(ActivityPeriod(ActivityState.ACTIVE, active_started_at))

    return timeline


def close_open_period(
    timeline: ActivityTimeline,
    active_started_at: datetime,
    last_activity_at: datetime,
    until: datetime,
    idle_threshold: timedelta,
) -> ActivityTimeline:
    """Close the ongoing (trailing open ACTIVE) period at ``until``.

    Called at check-out / exit / idle flush. If the last activity was within
    the threshold, the open period simply runs to ``until``. If the user went
    quiet for >= threshold, the open period is clipped at
    ``last_activity_at + threshold`` and a trailing IDLE period runs to
    ``until``.
    """
    if not timeline or last_activity_at is None:
        return timeline  # nothing ever happened — nothing to close

    tail = timeline[-1]
    if tail.is_open and tail.state is ActivityState.ACTIVE:
        if until - last_activity_at >= idle_threshold:
            tail.ended_at = last_activity_at + idle_threshold
            timeline.append(ActivityPeriod(ActivityState.IDLE, tail.ended_at, until))
        else:
            tail.ended_at = until
        return timeline

    # No trailing open period (unexpected projection state): append one that
    # runs to `until` from the last observed start as a best-effort close.
    start = active_started_at or last_activity_at
    if start is not None:
        timeline.append(ActivityPeriod(ActivityState.ACTIVE, start, until))
    return timeline
