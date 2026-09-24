"""Sync worker — queue drain with supervisor and backoff.

Threading discipline (docs/ENGINEERING_RULES.md §Threading Discipline):
* DB and provider I/O run off the Qt main thread.
* UI updates flow only via Qt signals.
* Uncaught exception is logged and the worker restarts with backoff.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.services.sync_service import SyncService

_logger = get_logger("sync.worker")

_RETRY_BACKOFF_SECONDS: tuple[int, ...] = (0, 30, 120, 300, 900)


class SyncWorker(QObject):
    """QObject that drains the sync queue from a worker thread."""

    synced = Signal(int)  # count synced this tick
    failed = Signal(int)  # count failed this tick
    connectivity_changed = Signal(str)
    error_occurred = Signal(str)

    def __init__(
        self,
        service: SyncService,
        *,
        poll_interval_ms: int = 30000,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._poll_ms = poll_interval_ms
        self._running = False
        self._paused = False
        self._attempt = 0
        self._timer: QTimer | None = None

    @Slot()
    def start(self) -> None:
        if self._running:
            return
        try:
            self._running = True
            self._paused = False
            self._attempt = 0
            self._timer = QTimer(self)
            self._timer.setInterval(self._poll_ms)
            self._timer.timeout.connect(self._tick)
            self._timer.start()
            _logger.info("sync worker started poll=%sms", self._poll_ms)
            # Kick once immediately (don't wait for first interval)
            QTimer.singleShot(500, self._tick)
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
        _logger.info("sync worker stopped")

    @Slot()
    def pause(self) -> None:
        self._paused = True
        _logger.info("sync worker paused")

    @Slot()
    def resume(self) -> None:
        self._paused = False
        _logger.info("sync worker resumed")

    @Slot()
    def handle_system_suspend(self) -> None:
        self.pause()

    @Slot()
    def handle_system_resume(self) -> None:
        self.resume()
        # Trigger immediate drain on resume (backoff still respected)
        QTimer.singleShot(200, self._tick)

    @Slot()
    def _tick(self) -> None:
        if not self._running or self._paused:
            return
        try:
            result = self._service.sync_once()
            synced = int(result.get("synced", 0))
            failed = int(result.get("failed", 0))
            skipped_offline = int(result.get("skipped_offline", 0))
            # Emit connectivity for tray/status (optional observer)
            try:
                conn = self._service.connectivity.value
                self.connectivity_changed.emit(conn)
            except Exception:  # noqa: S110
                pass

            if synced:
                self.synced.emit(synced)
                self._attempt = 0
            if failed:
                self.failed.emit(failed)
            if skipped_offline:
                # Offline/backoff skips are not errors — just debug
                _logger.debug("sync tick skipped offline/backoff")
        except Exception as exc:
            self._handle_failure(exc)

    def _handle_failure(self, exc: Exception) -> None:
        _logger.error("sync worker failure: %s", exc, exc_info=True)
        try:  # noqa: SIM105
            self.error_occurred.emit(str(exc))
        except Exception:  # noqa: S110
            pass
        self._attempt += 1
        delay = _RETRY_BACKOFF_SECONDS[
            min(self._attempt - 1, len(_RETRY_BACKOFF_SECONDS) - 1)
        ]
        _logger.info("sync worker restart attempt=%s delay=%ss", self._attempt, delay)
        self.stop()
        if delay == 0:
            self.start()
        else:
            QTimer.singleShot(delay * 1000, self.start)


class SyncWorkerSupervisor(QObject):
    """Owns the QThread for the sync worker."""

    synced = Signal(int)
    failed = Signal(int)
    connectivity_changed = Signal(str)
    error_occurred = Signal(str)

    def __init__(
        self,
        service: SyncService,
        *,
        poll_interval_ms: int = 30000,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        from PySide6.QtCore import QThread

        self._thread = QThread(self)
        self._worker = SyncWorker(service, poll_interval_ms=poll_interval_ms)
        self._worker.moveToThread(self._thread)
        self._worker.synced.connect(self.synced.emit)
        self._worker.failed.connect(self.failed.emit)
        self._worker.connectivity_changed.connect(self.connectivity_changed.emit)
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

    @property
    def worker(self) -> SyncWorker:
        return self._worker

    @property
    def worker_thread(self) -> object:
        return self._thread
