"""Unit tests for the event-driven drain trigger on :class:`SyncWorker`.

The poll timer is the retry and backoff path; ``request_sync`` is the low
latency path for the four user actions an observer watches. These tests pin
the two properties that make it safe to call from the GUI thread on every
click: bursts coalesce into one drain, and no drain ever overlaps another.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.workers.sync_worker import SyncWorker

if TYPE_CHECKING:
    import pytest


class FakeDebounce:
    """Stands in for the worker's single-shot QTimer, which needs a loop.

    Models QTimer semantics that matter here: it is single-shot, so it holds at
    most one pending fire, and calling ``start`` again *replaces* that pending
    fire rather than queueing a second one.
    """

    def __init__(self) -> None:
        self.start_calls = 0
        self.fires = 0
        self.running = False

    def start(self, _ms: int = 0) -> None:
        self.start_calls += 1
        self.fires = 1
        self.running = True

    def stop(self) -> None:
        self.fires = 0
        self.running = False


class FakeConnectivity:
    ONLINE = "ONLINE"
    SYNCING = "SYNCING"

    def __init__(self) -> None:
        self.value = "ONLINE"


class FakeSyncService:
    def __init__(self, *, ticks: list[object] | None = None) -> None:
        self.drains = 0
        self.ticks = ticks if ticks is not None else []
        self.connectivity = FakeConnectivity()
        self._reenter: object = None

    def sync_once(self) -> dict[str, int]:
        self.drains += 1
        self.ticks.append(self.drains)
        if self._reenter is not None:
            self._reenter()
        return {"synced": 1, "failed": 0, "skipped_offline": 0, "pending": 0}


def make_worker(
    service: FakeSyncService, monkeypatch: pytest.MonkeyPatch
) -> SyncWorker:
    worker = SyncWorker(service, workspace_provider=None, policy_service=None)
    worker._running = True  # bypass QThread startup; drive slots directly
    worker._debounce = FakeDebounce()  # type: ignore[assignment]
    return worker


def test_request_sync_schedules_a_drain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = make_worker(FakeSyncService(), monkeypatch)

    worker.request_sync()

    assert worker._debounce.fires == 1


def test_a_burst_of_requests_coalesces_into_one_drain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One click commits several rows; they must not become several drains."""
    worker = make_worker(FakeSyncService(), monkeypatch)

    for _ in range(5):
        worker.request_sync()

    # The timer is re-armed each time, but it is single-shot, so one drain is
    # pending no matter how many rows the action committed.
    assert worker._debounce.fires == 1
    assert worker._debounce.start_calls == 5


def test_no_drain_overlaps_another(monkeypatch: pytest.MonkeyPatch) -> None:
    """A request landing mid-drain must not re-enter ``sync_once``."""
    service = FakeSyncService()
    worker = make_worker(service, monkeypatch)
    reentrant_requests: list[None] = []
    service._reenter = lambda: reentrant_requests.append(worker.request_sync())

    worker.request_sync()
    worker._debounce.fires = 0  # the timer fired; drain begins
    worker._tick()

    assert service.drains == 1, "re-entrant request must not start a second drain"


def test_a_request_during_a_drain_is_re_armed_afterwards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Work committed after the drain read the queue is not left to the poll."""
    service = FakeSyncService()
    worker = make_worker(service, monkeypatch)
    service._reenter = worker.request_sync

    worker._tick()

    assert service.drains == 1
    assert worker._debounce.fires == 1, "drain re-arms to catch what it missed"


def test_a_paused_worker_ignores_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Suspend must still mean 'do no network work'."""
    worker = make_worker(FakeSyncService(), monkeypatch)
    worker.pause()

    worker.request_sync()

    assert worker._debounce.fires == 0


def test_a_stopped_worker_ignores_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    worker = make_worker(FakeSyncService(), monkeypatch)
    worker.stop()

    worker.request_sync()

    assert worker._debounce.fires == 0


def test_stop_clears_a_queued_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pending re-arm must not outlive the worker it was queued against."""
    service = FakeSyncService()
    worker = make_worker(service, monkeypatch)
    service._reenter = worker.request_sync

    worker._draining = True
    worker.request_sync()
    assert worker._pending_tick is True

    worker.stop()

    assert worker._pending_tick is False
    assert worker._debounce.running is False
