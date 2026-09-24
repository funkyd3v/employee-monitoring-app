"""Cleanup worker — periodic post-sync deletion with supervisor.

Threading discipline (docs/ENGINEERING_RULES.md §Threading Discipline):
worker runs off the Qt main thread; UI via signals only.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.services.cleanup_service import CleanupService

_logger = get_logger("cleanup.worker")

_RETRY_BACKOFF_SECONDS: tuple[int, ...] = (0, 30, 120, 300, 900)


class CleanupWorker(QObject):
    """QObject that runs :class:`CleanupService.run_once` periodically."""

    cleaned = Signal(int)  # total deleted this tick (files+queue)
    error_occurred = Signal(str)

    def __init__(
        self,
        service: CleanupService,
        *,
        poll_interval_ms: int = 300000,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._poll_ms = poll_interval_ms
        self._running = False
        self._attempt = 0
        self._timer: QTimer | None = None

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
            _logger.info("cleanup worker started poll=%sms", self._poll_ms)
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
        _logger.info("cleanup worker stopped")

    @Slot()
    def _tick(self) -> None:
        if not self._running:
            return
        try:
            result = self._service.run_once()
            total = int(result.get("deleted_files", 0)) + int(
                result.get("deleted_queue", 0)
            )
            if total:
                self.cleaned.emit(total)
                self._attempt = 0
        except Exception as exc:
            self._handle_failure(exc)

    def _handle_failure(self, exc: Exception) -> None:
        _logger.error("cleanup worker failure: %s", exc, exc_info=True)
        try:  # noqa: SIM105
            self.error_occurred.emit(str(exc))
        except Exception:  # noqa: S110
            pass
        self._attempt += 1
        delay = _RETRY_BACKOFF_SECONDS[
            min(self._attempt - 1, len(_RETRY_BACKOFF_SECONDS) - 1)
        ]
        _logger.info(
            "cleanup worker restart attempt=%s delay=%ss",
            self._attempt,
            delay,
        )
        self.stop()
        if delay == 0:
            self.start()
        else:
            QTimer.singleShot(delay * 1000, self.start)


class CleanupWorkerSupervisor(QObject):
    """Owns the QThread for the cleanup worker."""

    cleaned = Signal(int)
    error_occurred = Signal(str)

    def __init__(
        self,
        service: CleanupService,
        *,
        poll_interval_ms: int = 300000,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        from PySide6.QtCore import QThread

        self._thread = QThread(self)
        self._worker = CleanupWorker(service, poll_interval_ms=poll_interval_ms)
        self._worker.moveToThread(self._thread)
        self._worker.cleaned.connect(self.cleaned.emit)
        self._worker.error_occurred.connect(self.error_occurred.emit)
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

    @property
    def worker(self) -> CleanupWorker:
        return self._worker

    @property
    def worker_thread(self) -> object:
        return self._thread
