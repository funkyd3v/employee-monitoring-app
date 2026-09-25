from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

from app.workers.screenshot_worker import ScreenshotWorker


class FakeScreenshotService:
    def __init__(self) -> None:
        self.captures: list[datetime] = []

    def capture_once(self, *, at: datetime) -> Path:
        self.captures.append(at)
        return Path(f"capture-{len(self.captures)}.jpg")


def test_break_duration_is_excluded_from_screenshot_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduled_start = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
    now = scheduled_start

    def utc_now() -> datetime:
        return now

    monkeypatch.setattr("app.core.clock.utc_now", utc_now)

    service = FakeScreenshotService()
    worker = ScreenshotWorker(service)
    worker._running = True
    worker.set_schedule(scheduled_start, 60)

    now = scheduled_start + timedelta(minutes=1)
    worker._tick()
    now = scheduled_start + timedelta(minutes=2)
    worker._tick()

    worker.pause()
    now = scheduled_start + timedelta(minutes=7)
    worker._tick()
    worker.resume()
    worker._tick()

    assert service.captures == [
        scheduled_start + timedelta(minutes=1),
        scheduled_start + timedelta(minutes=2),
    ]

    now = scheduled_start + timedelta(minutes=8)
    worker._tick()
    assert service.captures[-1] == scheduled_start + timedelta(minutes=8)
