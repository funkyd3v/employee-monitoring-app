"""Screenshot worker — drift-resistant scheduler with supervisor.

Rules (docs/ENGINEERING_RULES.md):
* ``next_capture_at = scheduled_start + n * interval`` — never ``sleep(interval)``.
* Off Qt thread; UI via signals only; supervisor restarts on failure.
* Pauses during BREAK / after COMPLETED / on sleep-lock.
"""

from __future__ import annotations

import contextlib
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from app.core.logging import get_logger
from app.domain.screenshots.scheduling import next_capture_at

if TYPE_CHECKING:
    from app.services.screenshot_service import ScreenshotService

_logger = get_logger("screenshots.worker")

_RETRY_BACKOFF_SECONDS: tuple[int, ...] = (0, 30, 120, 300, 900)


class ScreenshotWorker(QObject):
    """Runs the drift-resistant loop on a dedicated QThread."""

    captured = Signal(str)  # file_path
    error_occurred = Signal(str)

    def __init__(
        self,
        service: ScreenshotService,
        *,
        poll_interval_ms: int = 1000,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._poll_ms = poll_interval_ms
        self._running = False
        self._paused = False
        self._scheduled_start: datetime | None = None
        self._interval: timedelta | None = None
        self._n = -1  # last emitted index
        self._paused_at: datetime | None = None
        self._attempt = 0
        self._timer: QTimer | None = None

    # ── Public controls (queued) ────────────────────────────────────

    @Slot()
    def start(self) -> None:
        if self._running:
            return
        try:
            self._running = True
            self._attempt = 0
            self._timer = QTimer(self)
            self._timer.setInterval(self._poll_ms)
            self._timer.timeout.connect(self._tick)
            self._timer.start()
            _logger.info("screenshot worker started poll=%sms", self._poll_ms)
        except Exception as exc:
            self._handle_failure(exc)

    @Slot()
    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        _logger.info("screenshot worker stopped")

    @Slot()
    def pause(self) -> None:
        """Pause captures (BREAK / sleep)."""
        if self._paused:
            return
        from app.core.clock import utc_now

        self._paused = True
        self._paused_at = utc_now()
        _logger.info("screenshot worker paused")

    @Slot()
    def resume(self) -> None:
        if not self._paused:
            return
        from app.core.clock import utc_now

        now = utc_now()
        if (
            self._paused_at is not None
            and self._scheduled_start is not None
            and self._interval is not None
        ):
            paused_for = now - self._paused_at
            if paused_for > timedelta(0):
                self._scheduled_start += paused_for
        self._paused_at = None
        self._paused = False
        _logger.info("screenshot worker resumed")

    @Slot(object, object)
    def set_schedule(self, scheduled_start: object, interval_seconds: object) -> None:
        """(Re)configure the drift schedule — called on Check In / interval change."""
        try:
            if not isinstance(scheduled_start, datetime) or not isinstance(
                interval_seconds, int
            ):
                _logger.warning("invalid schedule payload — ignoring")
                return
            self._scheduled_start = scheduled_start
            self._interval = timedelta(seconds=interval_seconds)
            self._n = -1
            self._paused = False
            self._paused_at = None
            _logger.info(
                "screenshot schedule set start=%s interval=%ss",
                scheduled_start,
                interval_seconds,
            )
        except Exception as exc:
            _logger.error("set_schedule failed: %s", exc, exc_info=True)

    @Slot()
    def handle_system_suspend(self) -> None:
        self.pause()

    @Slot()
    def handle_system_resume(self) -> None:
        # No false capture on resume — just allow next due tick to fire
        self.resume()

    # ── Tick ────────────────────────────────────────────────────────

    @Slot()
    def _tick(self) -> None:
        if not self._running or self._paused:
            return
        if self._scheduled_start is None or self._interval is None:
            return
        try:
            from app.core.clock import utc_now

            now = utc_now()
            # Drift-resistant: emit all due indices (catch-up cap in scheduling)
            # We replicate scheduling logic inline to avoid import coupling.
            target = next_capture_at(self._scheduled_start, self._interval, self._n + 1)
            # Tolerance 2s per spec
            if now < (target - timedelta(seconds=2)):
                return

            # Due — capture via service (service enforces paused/no-session)
            # Advance n optimistically before capture so next tick targets correctly
            self._n += 1
            result = self._service.capture_once(at=now)
            if result is not None:
                self.captured.emit(str(result))
                self._attempt = 0  # success resets backoff
            # If result is None due to paused/low-disk/provider failure, keep n advanced
            # so schedule does not bunch up — a missed slot is a missed slot (policy).
            # But if service.can_capture() was False, we still consumed the slot; that's intentional.
            # Catch-up: if we were suspended long, emit extra slots up to cap
            # by looping while still due (with guard)
            for _ in range(4):  # up to total 5 per scheduling.captures_due cap
                nxt = next_capture_at(
                    self._scheduled_start, self._interval, self._n + 1
                )
                if now >= nxt:
                    self._n += 1
                    try:
                        r = self._service.capture_once(at=now)
                        if r is not None:
                            self.captured.emit(str(r))
                    except Exception:  # noqa: S110
                        pass
                else:
                    break

        except Exception as exc:
            self._handle_failure(exc)

    def _handle_failure(self, exc: Exception) -> None:
        _logger.error("screenshot worker failure: %s", exc, exc_info=True)
        with contextlib.suppress(Exception):
            self.error_occurred.emit(str(exc))
        self._attempt += 1
        delay = _RETRY_BACKOFF_SECONDS[
            min(self._attempt - 1, len(_RETRY_BACKOFF_SECONDS) - 1)
        ]
        _logger.info(
            "screenshot worker restart attempt=%s delay=%ss", self._attempt, delay
        )
        self.stop()
        if delay == 0:
            self.start()
        else:
            QTimer.singleShot(delay * 1000, self.start)


class ScreenshotWorkerSupervisor(QObject):
    """Owns the QThread for the screenshot worker."""

    captured = Signal(str)
    error_occurred = Signal(str)
    _request_schedule = Signal(object, object)

    def __init__(
        self,
        service: ScreenshotService,
        *,
        poll_interval_ms: int = 1000,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        from PySide6.QtCore import QThread

        self._thread = QThread(self)
        self._worker = ScreenshotWorker(service, poll_interval_ms=poll_interval_ms)
        self._worker.moveToThread(self._thread)
        self._worker.captured.connect(self.captured.emit)
        self._worker.error_occurred.connect(self.error_occurred.emit)
        self._request_schedule.connect(self._worker.set_schedule)
        self._thread.started.connect(self._worker.start)
        self._thread.finished.connect(self._worker.deleteLater)

    def start(self) -> None:
        if not self._thread.isRunning():
            self._thread.start()

    def stop(self) -> None:
        if self._thread.isRunning():
            from PySide6.QtCore import QMetaObject, Qt

            QMetaObject.invokeMethod(
                self._worker, "stop", Qt.ConnectionType.QueuedConnection
            )
            time.sleep(0.05)
            self._thread.quit()
            self._thread.wait(2000)

    def pause(self) -> None:
        from PySide6.QtCore import QMetaObject, Qt

        QMetaObject.invokeMethod(
            self._worker, "pause", Qt.ConnectionType.QueuedConnection
        )

    def resume(self) -> None:
        from PySide6.QtCore import QMetaObject, Qt

        QMetaObject.invokeMethod(
            self._worker, "resume", Qt.ConnectionType.QueuedConnection
        )

    def set_schedule(self, scheduled_start: datetime, interval_seconds: int) -> None:
        self._request_schedule.emit(scheduled_start, interval_seconds)

    @property
    def worker(self) -> ScreenshotWorker:
        return self._worker

    @property
    def worker_thread(self) -> object:
        return self._thread
