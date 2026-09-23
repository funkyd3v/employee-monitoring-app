"""Application lifecycle: ordered, extensible start/shutdown sequencing.

Mirrors docs/ARCHITECTURE.md § Application lifecycle:

    Startup:  config → logging → database → recover state → services
              → auth restore → session restore → workers → UI
    Shutdown: screenshot scheduler → activity worker → flush state
              → sync/cleanup workers → database → exit

Later phases register steps; order is preserved in the order added and
shutdown runs in strict reverse. Failure isolation: a failed startup step
logs, cleans up everything started so far, and surfaces a typed error —
one broken subsystem must never take the app down silently.
"""

from __future__ import annotations

from collections.abc import Callable

from app.core.exceptions import ConfigurationError
from app.core.logging import get_logger

StartFn = Callable[["LifecycleContext"], None]
ShutdownFn = Callable[["LifecycleContext"], None]


class LifecycleContext:
    """Carries the container through the lifecycle; steps may attach state.

    Later phases add typed accessors (database handle, worker handles) here
    without changing the sequencing logic.
    """

    def __init__(self) -> None:
        self._state: dict[str, object] = {}

    def set(self, key: str, value: object) -> None:
        self._state[key] = value

    def get(self, key: str, default: object = None) -> object:
        return self._state.get(key, default)


class _Step:
    __slots__ = ("name", "shutdown", "start")

    def __init__(self, name: str, start: StartFn, shutdown: ShutdownFn | None) -> None:
        self.name = name
        self.start = start
        self.shutdown = shutdown


class Lifecycle:
    """Ordered collection of start/shutdown steps."""

    def __init__(self, context: LifecycleContext | None = None) -> None:
        self._context = context or LifecycleContext()
        self._steps: list[_Step] = []
        self._started: list[str] = []
        self._logger = get_logger("lifecycle")

    @property
    def context(self) -> LifecycleContext:
        return self._context

    def add(
        self,
        name: str,
        start: StartFn | None = None,
        shutdown: ShutdownFn | None = None,
    ) -> None:
        """Register a lifecycle step. Order of registration = start order."""
        self._steps.append(_Step(name, start or self._noop, shutdown))

    def start(self) -> None:
        """Run startup steps in order; on failure, unwind in reverse."""
        for step in self._steps:
            try:
                step.start(self._context)
                self._started.append(step.name)
                self._logger.info("startup: %s", step.name)
            except Exception as exc:
                self._logger.critical(
                    "startup step %s failed: %s", step.name, exc, exc_info=True
                )
                self._unwind(up_to=step.name)
                raise ConfigurationError(
                    f"Startup failed at {step.name}: {exc}"
                ) from exc

    def shutdown(self) -> None:
        """Run shutdown steps in reverse start order; never raises."""
        pending = [s for s in self._steps if s.name in self._started]
        for step in reversed(pending):
            if step.shutdown is None:
                continue
            try:
                step.shutdown(self._context)
                self._logger.info("shutdown: %s", step.name)
            except Exception:
                self._logger.error("shutdown step %s failed", step.name, exc_info=True)

    def _unwind(self, up_to: str) -> None:
        started = [s for s in self._steps if s.name in self._started]
        for step in reversed(started):
            if step.shutdown and step.name != up_to:
                try:
                    step.shutdown(self._context)
                except Exception:
                    self._logger.error("unwind of %s failed", step.name, exc_info=True)

    @staticmethod
    def _noop(context: LifecycleContext) -> None:  # noqa: ARG004
        return None
