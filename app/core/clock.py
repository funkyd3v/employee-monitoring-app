"""Time source for the application.

Encoding of docs/ENGINEERING_RULES.md §Time & clock handling:

* Persisted timestamps (DB rows, events): UTC, timezone-aware datetimes.
* Elapsed-duration calculations (session length, idle duration): the
  monotonic clock, immune to wall-clock adjustments (NTP, DST, manual
  changes).
* UI display: local wall time is a *render-time* concern only; domain logic
  never sees local time.

All domain/services code should obtain *now* through this module so tests
can substitute a controlled clock.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

UtcNow = Callable[[], datetime]
MonotonicNow = Callable[[], float]


def utc_now() -> datetime:
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


def monotonic_now() -> float:
    """Current monotonic counter (immune to wall-clock adjustments)."""
    return time.monotonic()


@dataclass(frozen=True)
class Clock:
    """Injected time source: UTC wall clock + monotonic counter.

    Domain entities and services accept a ``Clock`` (default
    :data:`SYSTEM_CLOCK`) so tests can freeze or step time deterministically.
    """

    utc: UtcNow = utc_now
    monotonic: MonotonicNow = monotonic_now


SYSTEM_CLOCK = Clock()
