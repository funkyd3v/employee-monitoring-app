"""Activity provider contract and dummy implementations.

Backend-readiness (docs/ARCHITECTURE.md §Backend-readiness): the activity
engine sits behind an interface so a future backend-driven policy (e.g.
server-pushed idle threshold) can be slotted without touching services.

Privacy boundary (docs/ENGINEERING_RULES.md §Privacy boundary,
docs/SECURITY_PRIVACY.md): providers emit *that* input occurred, never *what*.
No key values, characters, coordinates, or clipboard content are represented
in the signal — the provider discards them within the input hook tick.

Windows-specific provider lives in
``app/infrastructure/activity/windows_activity_provider.py``; this module
holds the cross-platform contract and the test/local dummy used on
non-Windows dev machines.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


class ActivityProvider(ABC):
    """Contract for OS-level activity detection."""

    @abstractmethod
    def start(self) -> None:
        """Begin listening for input activity (idempotent)."""

    @abstractmethod
    def stop(self) -> None:
        """Stop listening and release OS resources (idempotent)."""

    @abstractmethod
    def set_activity_callback(self, callback: Callable[[], None] | None) -> None:
        """Register the callback invoked on *any* input activity.

        The callback carries no payload — it is a pure tick for
        ``INPUT_ACTIVITY_DETECTED``. Implementations must call it from
        either a dedicated thread or via a queued signal, never with raw
        input data.
        """

    @abstractmethod
    def get_idle_seconds(self) -> float | None:
        """Seconds since last OS input per the authoritative fallback.

        Returns ``None`` when the fallback is unavailable (e.g. non-Windows
        dummy). Services treat ``None`` as "fallback unsupported" and rely
        purely on hook callbacks.
        """


class DummyActivityProvider(ActivityProvider):
    """Manual-trigger provider for tests and non-Windows dev."""

    def __init__(self) -> None:
        self._callback: Callable[[], None] | None = None
        self._running = False
        self._idle_override: float | None = None

    def start(self) -> None:
        self._running = True

    def stop(self) -> None:
        self._running = False

    def set_activity_callback(self, callback: Callable[[], None] | None) -> None:
        self._callback = callback

    def get_idle_seconds(self) -> float | None:
        return self._idle_override

    # Test helpers ---------------------------------------------------------

    def set_idle_seconds(self, seconds: float | None) -> None:
        """Force the next ``get_idle_seconds`` return value."""
        self._idle_override = seconds

    def simulate_activity(self) -> None:
        """Fire the registered callback as if input occurred."""
        if self._callback is not None and self._running:
            self._callback()

    @property
    def running(self) -> bool:
        return self._running
