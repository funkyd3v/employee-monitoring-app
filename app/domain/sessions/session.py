"""Work-session domain: entities and pure duration math.

Timer correctness (docs/ENGINEERING_RULES.md §Timer Correctness): the UI
timer is never accumulated in memory. Elapsed work time is always recomputed
from the persisted session start minus accumulated break time:

    elapsed_work = now - started_at - accumulated_break_seconds

Time comes in as a parameter (or a :class:`SessionClock` for live
projection) so tests control it deterministically.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

UtcNow = Callable[[], datetime]


class WorkSessionStatus(StrEnum):
    """Persisted status of one Check-In → Check-Out cycle."""

    WORKING = "WORKING"
    BREAK = "BREAK"
    COMPLETED = "COMPLETED"


class ActivityState(StrEnum):
    """Active/idle sub-state of a WORKING session (docs/STATE_MACHINE.md).

    Never changes which buttons are shown — only what is written to the
    activity timeline and whether the idle screenshot border applies.
    """

    ACTIVE = "ACTIVE"
    IDLE = "IDLE"


@dataclass(frozen=True)
class Break:
    """A single break interval within a session.

    Duration is stored explicitly because it is persisted that way; elapsed
    work-time math subtracts the *sum* of accumulated break seconds, per the
    timer-correctness rule.
    """

    session_id: int
    started_at: datetime
    ended_at: datetime | None = None
    duration_seconds: int = 0
    id: int | None = None

    @property
    def is_open(self) -> bool:
        return self.ended_at is None

    def close(self, ended_at: datetime) -> Break:
        if self.ended_at is not None:
            raise ValueError("break is already closed")
        duration = max(0, int((ended_at - self.started_at).total_seconds()))
        return Break(
            id=self.id,
            session_id=self.session_id,
            started_at=self.started_at,
            ended_at=ended_at,
            duration_seconds=duration,
        )


@dataclass
class WorkSession:
    """A Check-In → Check-Out cycle with its accumulated breaks.

    ``ended_at`` and ``total_work_seconds`` are set on Check Out. Until then
    elapsed work time is derived by subtracting the accumulated break
    seconds from the wall-clock span — never accumulated in memory.
    """

    user_id: int
    started_at: datetime
    status: WorkSessionStatus = WorkSessionStatus.WORKING
    id: int | None = None
    ended_at: datetime | None = None
    total_work_seconds: int = 0
    breaks: list[Break] = field(default_factory=list)

    @property
    def accumulated_break_seconds(self) -> int:
        return sum(b.duration_seconds for b in self.breaks)

    def elapsed_work_seconds(self, now: datetime) -> int:
        """Drift-free work seconds at ``now`` (docs §Timer Correctness)."""
        if self.status is WorkSessionStatus.COMPLETED and self.ended_at is not None:
            return self.total_work_seconds
        return self._elapsed_at(now)

    def live_elapsed(self, utc: UtcNow) -> int:
        """Current work seconds for the UI timer projection."""
        return self.elapsed_work_seconds(utc())

    def checkout(self, now: datetime) -> WorkSession:
        """Close the session at ``now``, freezing total work time."""
        self.total_work_seconds = max(0, self._elapsed_at(now))
        self.ended_at = now
        self.status = WorkSessionStatus.COMPLETED
        return self

    @property
    def is_active(self) -> bool:
        return self.status in (
            WorkSessionStatus.WORKING,
            WorkSessionStatus.BREAK,
        )

    def _elapsed_at(self, now: datetime) -> int:
        total = max(0, int((now - self.started_at).total_seconds()))
        return max(0, total - self.accumulated_break_seconds)


def create_session(user_id: int, started_at: datetime) -> WorkSession:
    """Factory for a brand-new WORKING session."""
    return WorkSession(user_id=user_id, started_at=started_at)
