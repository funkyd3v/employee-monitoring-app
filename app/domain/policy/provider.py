"""Remote policy: the settings an operator pushes to the agent.

``ServerPolicy`` (app/config/settings.py) is the read model — its shape was
fixed before any backend existed, and services read it without knowing where it
came from. This module adds the missing half: the *port* those settings arrive
through, so a second value can be delivered later without a service ever
importing an HTTP client.

Local mode: :class:`LocalConfigProvider` simply hands back the object the app
was configured with — a no-op that keeps every call site free of
``if mode == "api"`` branches. API mode: :class:`ApiConfigProvider` reads the
backend's ``/agent/config`` and returns a validated policy, or None when the
server has nothing new to say.

An operator can therefore change the screenshot interval for a fleet of
installed agents by changing server configuration — no rebuild, no redeploy.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.config.settings import ServerPolicy


class ConfigProvider(ABC):
    """Contract for obtaining the effective :class:`ServerPolicy`."""

    @abstractmethod
    def current_policy(self) -> ServerPolicy:
        """Return the policy to run with right now.

        Must not raise: a backend that is unreachable or answers with
        something unexpected leaves the local defaults in force, because an
        agent with no policy is a degraded agent and an agent that refuses to
        start is a broken one.
        """


class LocalConfigProvider(ConfigProvider):
    """``mode: "local"``: the configured defaults are authoritative."""

    def __init__(self, policy: ServerPolicy) -> None:
        self._policy = policy

    def current_policy(self) -> ServerPolicy:
        return self._policy


__all__ = ["ConfigProvider", "LocalConfigProvider"]
