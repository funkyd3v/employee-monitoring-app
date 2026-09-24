"""Activity worker — off-Qt-thread monitoring with supervisor.

Threading discipline (docs/ENGINEERING_RULES.md §Threading Discipline):
* All provider polling and DB I/O run off the Qt main thread.
* UI updates flow only via Qt signals — the worker never touches a widget.
* An uncaught exception is logged and the worker restarts with backoff rather
  than crashing the app.

Sleep/lock handling (docs §Sleep / lock / resume): workstation lock/suspend
does not generate false activity; state is recalculated on resume.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from app.core.logging import get_logger
from app.domain.activity.activity import ActivityState

if TYPE_CHECKING:
    from app.domain.activity.provider import ActivityProvider
    from app.services.activity_service import ActivityService

_logger = get_logger("activity.worker")

# Backoff schedule mirrors screenshot/sync workers (docs/ENGINEERING_RULES.md §Retry strategy)
_RETRY_BACKOFF_SECONDS: tuple[int, ...] = (0, 30, 120, 300, 900)


class ActivityWorker(QObject):
    """QObject that drives the provider + service from a worker thread.

    Instantiate on the worker ``QThread`` and call :meth:`start`. The worker
    polls ``ActivityService.check_idle`` on a ``QTimer`` and forwards hook
    callbacks via a queued signal. The controller connects
    :attr:`state_changed` to update the dashboard/tray pill.
    """

    state_changed = Signal(str)  # ActivityState.value
    error_occurred = Signal(str)

    def __init__(
        self,
        provider: ActivityProvider,
        service: ActivityService,
        *,
        poll_interval_ms: int = 5000,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._provider = provider
        self._service = service
        self._poll_ms = poll_interval_ms
        self._running = False
        self._attempt = 0
        self._timer: QTimer | None = None
        self._fallback_timer: QTimer | None = None

    # ── Public controls (invoked from controller/lifecycle thread) ─────────

    @Slot()
    def start(self) -> None:
        """Start provider and timers (idempotent, queued to worker thread)."""
        if self._running:
            return
        try:
            self._provider.set_activity_callback(self._on_activity)
            self._provider.start()
            self._timer = QTimer(self)
            self._timer.setInterval(self._poll_ms)
            self._timer.timeout.connect(self._poll_idle)
            self._timer.start()

            # Fallback poll for Win32 idle (authoritative, hook-independent)
            self._fallback_timer = QTimer(self)
            self._fallback_timer.setInterval(self._poll_ms)
            self._fallback_timer.timeout.connect(self._poll_fallback)
            self._fallback_timer.start()

            self._running = True
            self._attempt = 0
            _logger.info("activity worker started poll=%sms", self._poll_ms)
        except Exception as exc:
            self._handle_failure(exc)

    @Slot()
    def stop(self) -> None:
        """Stop provider and timers (idempotent)."""
        if not self._running:
            return
        self._running = False
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        if self._fallback_timer is not None:
            self._fallback_timer.stop()
            self._fallback_timer = None
        try:
            self._provider.set_activity_callback(None)
            self._provider.stop()
        except Exception:
            _logger.debug("provider stop raised during worker stop", exc_info=True)
        _logger.info("activity worker stopped")

    @Slot()
    def pause(self) -> None:
        """Pause idle polling while on BREAK (provider keeps listening but ticks ignored)."""
        if self._timer is not None:
            self._timer.stop()
        if self._fallback_timer is not None:
            self._fallback_timer.stop()

    @Slot()
    def resume_polling(self) -> None:
        if not self._running:
            return
        if self._timer is not None and not self._timer.isActive():
            self._timer.start()
        if self._fallback_timer is not None and not self._fallback_timer.isActive():
            self._fallback_timer.start()

    @Slot()
    def handle_system_resume(self) -> None:
        """Called on WORKSTATION_UNLOCKED / SYSTEM_RESUME — recalc without false tick."""
        try:
            # Don't synthesize activity on resume — just re-evaluate idle.
            new_state = self._service.check_idle()
            if new_state is not None:
                self.state_changed.emit(new_state.value)
        except Exception as exc:
            self._handle_failure(exc)

    @Slot()
    def handle_system_suspend(self) -> None:
        """On suspend/lock — pause polling, preserve timestamps."""
        self.pause()

    # ── Internal slots ─────────────────────────────────────────────────────

    @Slot()
    def _on_activity(self) -> None:
        if not self._running:
            return
        try:
            new_state = self._service.on_activity()
            if new_state is not None:
                self.state_changed.emit(new_state.value)
            # Also emit ACTIVE if we are currently ACTIVE but service didn't
            # need a DB transition — the pill should stay ACTIVE.
            elif self._service.current_state() is ActivityState.ACTIVE:
                pass
        except Exception as exc:
            self._handle_failure(exc)

    @Slot()
    def _poll_idle(self) -> None:
        if not self._running:
            return
        try:
            new_state = self._service.check_idle()
            if new_state is not None:
                self.state_changed.emit(new_state.value)
        except Exception as exc:
            self._handle_failure(exc)

    @Slot()
    def _poll_fallback(self) -> None:
        """Authoritative check via GetLastInputInfo — catches elevated-window misses.

        If the fallback reports idle >= threshold while the hook still thinks
        ACTIVE, force the transition. This is the ``pynput`` ↔ Win32
        reconciliation described in docs/TECH_STACK.md and
        docs/SECURITY_PRIVACY.md §Risks.
        """
        if not self._running:
            return
        try:
            idle = self._provider.get_idle_seconds()
            if idle is None:
                return
            threshold = self._service.idle_threshold.total_seconds()
            if idle >= threshold:
                new_state = self._service.check_idle()
                if new_state is not None:
                    self.state_changed.emit(new_state.value)
            else:
                # Fallback says ACTIVE recent — if service thinks IDLE, activity resumed.
                # The hook should have caught it, but synthesize if it missed.
                if self._service.current_state() is ActivityState.IDLE:
                    new_state = self._service.on_activity()
                    if new_state is not None:
                        self.state_changed.emit(new_state.value)
        except Exception as exc:
            # Fallback is best-effort; never crash the worker for it.
            _logger.debug("fallback poll raised: %s", exc, exc_info=True)

    def _handle_failure(self, exc: Exception) -> None:
        _logger.error("activity worker failure: %s", exc, exc_info=True)
        self.error_occurred.emit(str(exc))
        # Supervisor: restart with backoff (docs §Threading Discipline)
        self._attempt += 1
        delay = _RETRY_BACKOFF_SECONDS[min(self._attempt - 1, len(_RETRY_BACKOFF_SECONDS) - 1)]
        _logger.info("activity worker restart attempt=%s delay=%ss", self._attempt, delay)
        self.stop()
        if delay == 0:
            self.start()
        else:
            QTimer.singleShot(delay * 1000, self.start)


class ActivityWorkerSupervisor(QObject):
    """Supervisor that owns the QThread for the worker.

    The controller creates this on the main thread; it moves the worker to a
    dedicated ``QThread`` and manages start/stop lifecycle hooks. UI code
    never interacts with the worker directly — only via the supervisor's
    forwarded signals.
    """

    state_changed = Signal(str)
    error_occurred = Signal(str)

    def __init__(
        self,
        provider: ActivityProvider,
        service: ActivityService,
        *,
        poll_interval_ms: int = 5000,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        from PySide6.QtCore import (
            QThread,  # local import to keep module importable without Qt loop
        )

        self._thread = QThread(self)
        self._worker = ActivityWorker(
            provider, service, poll_interval_ms=poll_interval_ms
        )
        self._worker.moveToThread(self._thread)
        self._worker.state_changed.connect(self.state_changed.emit)
        self._worker.error_occurred.connect(self.error_occurred.emit)
        self._thread.started.connect(self._worker.start)
        # Ensure worker stops before thread quits
        self._thread.finished.connect(self._worker.deleteLater)

    def start(self) -> None:
        if not self._thread.isRunning():
            self._thread.start()

    def stop(self) -> None:
        if self._thread.isRunning():
            # Invoke stop on worker thread, then quit thread
            from PySide6.QtCore import QMetaObject, Qt

            QMetaObject.invokeMethod(self._worker, "stop", Qt.ConnectionType.QueuedConnection)
            # Give worker a moment to stop gracefully
            time.sleep(0.05)
            self._thread.quit()
            self._thread.wait(2000)

    def pause(self) -> None:
        from PySide6.QtCore import QMetaObject, Qt

        QMetaObject.invokeMethod(self._worker, "pause", Qt.ConnectionType.QueuedConnection)

    def resume_polling(self) -> None:
        from PySide6.QtCore import QMetaObject, Qt

        QMetaObject.invokeMethod(self._worker, "resume_polling", Qt.ConnectionType.QueuedConnection)

    @property
    def worker(self) -> ActivityWorker:
        return self._worker

    @property
    def worker_thread(self) -> object:
        return self._thread
