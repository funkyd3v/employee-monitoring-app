"""Application event infrastructure.

UI updates must always flow through Qt signals (docs/ENGINEERING_RULES.md
§ Threading Discipline): workers publish domain events, the Qt layer
subscribes and repaints. The concrete Qt-backed bus is wired in
``app/core/container.py``; services depend only on this protocol so the
transport can be swapped (e.g. a thread-safe bus for tests) without
touching them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DomainEvent:
    """Immutable event value object published to interested subscribers."""

    name: str
    payload: dict[str, Any] = field(default_factory=dict)


Subscriber = Callable[[DomainEvent], None]


class EventBus(ABC):
    """Contract for publishing/subscribing to domain events."""

    @abstractmethod
    def subscribe(self, event_name: str, subscriber: Subscriber) -> None: ...

    @abstractmethod
    def publish(self, event: DomainEvent) -> None: ...
