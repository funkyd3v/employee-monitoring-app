"""Sync worker — queue drain with supervisor and backoff.

Threading discipline (docs/ENGINEERING_RULES.md §Threading Discipline):
* DB and provider I/O run off the Qt main thread.
* UI updates flow only via Qt signals.
* Uncaught exception is logged and the worker restarts with backoff.

This tick is also where the app's two "the server can change this without a
rebuild" features are read: the workspace label and the operator policy. Both
are network reads, both already belong on a worker thread, and both fail soft —
neither is allowed to disturb a queue drain. New server-driven settings would
be added here, which is the whole scalability claim in one place: nothing in
the UI, the services, or the domain learns that a new endpoint exists.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.domain.auth.workspace import WorkspaceProvider
    from app.services.sync_service import SyncService

_logger = get_logger("sync.worker")

_RETRY_BACKOFF_SECONDS: tuple[int, ...] = (0, 30, 120, 300, 900)

#: How long an event-driven request waits before draining, so that the rows a
#: single user action commits coalesce into one round of requests.
_REQUEST_DEBOUNCE_MS = 300


class SyncWorker(QObject):
    """QObject that drains the sync queue from a worker thread."""

    synced = Signal(int)  # count synced this tick
    failed = Signal(int)  # count failed this tick
    connectivity_changed = Signal(str)
    error_occurred = Signal(str)
    #: Emitted only when the backend reports a *different* workspace name, so
    #: the UI is not repainted once a poll cycle.
    workspace_changed = Signal(str)

    def __init__(
        self,
        service: SyncService,
        *,
        poll_interval_ms: int = 30000,
        workspace_provider: WorkspaceProvider | None = None,
        policy_service: object | None = None,
        debounce_ms: int = _REQUEST_DEBOUNCE_MS,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._poll_ms = poll_interval_ms
        self._running = False
        self._paused = False
        self._attempt = 0
        self._timer: QTimer | None = None
        self._workspace_provider = workspace_provider
        self._policy_service = policy_service
        self._last_workspace: str | None = None
        # A single action enqueues several rows (a check-in writes the work
        # session, a resume closes a break *and* updates the session), and
        # those commits land within microseconds of each other. Restarting one
        # single-shot timer collapses the burst into a single drain.
        self._debounce_ms = debounce_ms
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.timeout.connect(self._tick)
        # A drain is synchronous network I/O, so a second one must not start
        # underneath it. `SyncService` holds no lock of its own; serialising
        # here is what keeps two drains off the same pending rows.
        self._draining = False
        self._pending_tick = False

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
        self._debounce.stop()
        self._pending_tick = False
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
    def request_sync(self) -> None:
        """Drain now because something was just written locally.

        Called from the GUI thread via a queued connection, so it lands on the
        worker thread and is ordered against the poll timer — this never runs
        network I/O on the caller's thread. The poll timer is left running: it
        remains the retry and backoff path for rows this drain fails on.
        """
        if not self._running or self._paused:
            return
        if self._draining:
            # A drain is in flight and this request arrived after it read the
            # queue, so it would otherwise wait a whole poll interval. Re-arm
            # when the current drain finishes rather than stacking a second one.
            self._pending_tick = True
            _logger.debug("sync requested during drain; will re-arm")
            return
        self._debounce.start(self._debounce_ms)

    @Slot()
    def _tick(self) -> None:
        if not self._running or self._paused:
            return
        if self._draining:
            self._pending_tick = True
            return

        self._draining = True
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

            self._refresh_remote_settings(online=conn == "ONLINE")
        except Exception as exc:
            self._handle_failure(exc)
        finally:
            self._draining = False
            if self._pending_tick and self._running and not self._paused:
                self._pending_tick = False
                self._debounce.start(self._debounce_ms)

    def _refresh_remote_settings(self, *, online: bool) -> None:
        """Read the settings the server owns, after the drain.

        Both reads are best-effort by contract: a failure leaves the last good
        value in place and is logged at debug level, because neither a stale
        workspace label nor a stale interval is worth a retry storm.

        Skipped entirely when the backend is not reachable, so an offline
        machine does not spend a request budget proving it.
        """
        if not online:
            return

        if self._policy_service is not None:
            try:
                self._policy_service.refresh()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001 — never disturb a drain
                _logger.debug("remote policy refresh failed", exc_info=True)

        if self._workspace_provider is None:
            return

        try:
            user = self._workspace_provider.fetch_workspace()
        except Exception:  # noqa: BLE001 — never disturb a drain
            _logger.debug("workspace refresh failed", exc_info=True)
            return

        if user is None or user.workspace_name == self._last_workspace:
            return

        self._last_workspace = user.workspace_name
        _logger.info("workspace name updated from the backend")
        self.workspace_changed.emit(user.workspace_name or "")

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
    workspace_changed = Signal(str)

    def __init__(
        self,
        service: SyncService,
        *,
        poll_interval_ms: int = 30000,
        workspace_provider: WorkspaceProvider | None = None,
        policy_service: object | None = None,
    ) -> None:
        super().__init__()
        from PySide6.QtCore import QThread

        self._thread = QThread(self)
        self._worker = SyncWorker(
            service,
            poll_interval_ms=poll_interval_ms,
            workspace_provider=workspace_provider,
            policy_service=policy_service,
        )
        self._worker.moveToThread(self._thread)
        self._worker.synced.connect(self.synced.emit)
        self._worker.failed.connect(self.failed.emit)
        self._worker.connectivity_changed.connect(self.connectivity_changed.emit)
        self._worker.error_occurred.connect(self.error_occurred.emit)
        self._worker.workspace_changed.connect(self.workspace_changed.emit)
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

    def request_sync(self) -> None:
        """Ask for a drain without waiting for the next poll.

        Safe to call from the GUI thread: the queued connection hands the work
        to the worker thread and returns immediately, so the caller never
        blocks on the network.
        """
        if not self._thread.isRunning():
            return
        from PySide6.QtCore import QMetaObject, Qt

        QMetaObject.invokeMethod(
            self._worker, "request_sync", Qt.ConnectionType.QueuedConnection
        )

    @property
    def worker(self) -> SyncWorker:
        return self._worker

    @property
    def worker_thread(self) -> object:
        return self._thread
