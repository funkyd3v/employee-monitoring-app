"""Drift-resistant screenshot scheduling math.

Do NOT ``sleep(interval)`` in a loop — compute ``next_capture_at`` from
``scheduled_start + n * interval`` (docs/ENGINEERING_RULES.md §Screenshot
scheduling). Captures land close to 10:01:00, 10:02:00, ... not drifting to
10:02:07 via accumulated sleep.
"""

from __future__ import annotations

from datetime import datetime, timedelta


def next_capture_at(
    scheduled_start: datetime,
    interval: timedelta,
    n: int,
) -> datetime:
    """Return the wall-clock time for the ``n``-th capture (0-indexed).

    ``n=0`` is the first capture due after ``scheduled_start``.
    """
    if interval.total_seconds() <= 0:
        raise ValueError("interval must be positive")
    if n < 0:
        raise ValueError("n must be >= 0")
    if scheduled_start.tzinfo is None:
        raise ValueError("scheduled_start must be timezone-aware (UTC)")
    return scheduled_start + (interval * (n + 1))


def is_due(now: datetime, target: datetime, tolerance: timedelta | None = None) -> bool:
    """Return True when ``now`` has reached/passed ``target`` (with tolerance)."""
    if tolerance is None:
        tolerance = timedelta(seconds=1)
    # Due when now >= target - tolerance (allows ±1-2s scheduler granularity)
    return now >= (target - tolerance)


def captures_due(
    scheduled_start: datetime,
    interval: timedelta,
    now: datetime,
    last_n: int,
) -> list[int]:
    """Return indices of captures due between last emitted and ``now``.

    ``last_n`` is the last *emitted* index (or -1 if none). Returns at most
    one element in normal operation; multiple if the worker was suspended.
    """
    due: list[int] = []
    n = last_n + 1
    while True:
        target = next_capture_at(scheduled_start, interval, n)
        if now >= target:
            due.append(n)
            n += 1
            # Cap catch-up to avoid storm after long sleep — max 5
            if len(due) >= 5:
                break
        else:
            break
    return due
