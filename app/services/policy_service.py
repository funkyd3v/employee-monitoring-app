"""Remote policy: turning what the server says into what the agent does.

``ServerPolicy`` is the read model every service already depends on, so the
only thing this service has to do is *update* it and push the values that can
change while the app is running into the components that accept a setter.

That is the whole point of the exercise, and the reason it is worth being
explicit about which values are live:

* ``screenshot_interval_seconds`` and ``idle_threshold_seconds`` are applied
  immediately — both services expose a setter, and the drift-resistant
  scheduler re-reads the interval on its next tick.
* Everything else (retention, poll intervals, batch limit) is read once at
  construction, so a change is *recorded* and takes effect at the next start.

Applying half the policy and staying silent about the other half would be
worse than not applying it: an operator would reasonably assume a retention
change took hold immediately. The log line says exactly which is which.

No network access here — the read comes from the injected
:class:`~app.domain.policy.provider.ConfigProvider`, so this service is
testable with a stub and knows nothing about HTTP.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.config.settings import ServerPolicy
    from app.domain.policy.provider import ConfigProvider
    from app.services.activity_service import ActivityService
    from app.services.screenshot_service import ScreenshotService

_logger = get_logger("policy")

#: Policy fields a running agent can adopt without a restart, mapped to the
#: setter that applies them.
_LIVE_FIELDS = ("screenshot_interval_seconds", "idle_threshold_seconds")


class PolicyService:
    """Applies the effective :class:`ServerPolicy` to the running services."""

    def __init__(
        self,
        *,
        config_provider: ConfigProvider,
        settings: ServerPolicy,
        screenshot_service: ScreenshotService | None = None,
        activity_service: ActivityService | None = None,
    ) -> None:
        self._provider = config_provider
        self._policy = settings
        self._screenshots = screenshot_service
        self._activity = activity_service

    @property
    def policy(self) -> ServerPolicy:
        """The policy currently in force."""
        return self._policy

    def refresh(self) -> ServerPolicy:
        """Re-read the policy and apply whatever changed.

        Never raises. A policy read that fails leaves the previous values in
        force, which is the only safe answer for a background tick.
        """
        incoming = self._provider.current_policy()
        changed = [
            field
            for field in _LIVE_FIELDS
            if getattr(incoming, field, None) != getattr(self._policy, field, None)
        ]

        self._policy = incoming

        if not changed:
            return incoming

        if "screenshot_interval_seconds" in changed and self._screenshots is not None:
            self._screenshots.set_interval(incoming.screenshot_interval_seconds)
        if "idle_threshold_seconds" in changed and self._activity is not None:
            self._activity.set_idle_threshold(incoming.idle_threshold_seconds)

        _logger.info("remote policy applied: %s", ", ".join(changed))
        return incoming

    def apply(self, policy: ServerPolicy) -> ServerPolicy:
        """Adopt an explicit policy (used at startup and in tests)."""
        self._policy = policy
        if self._screenshots is not None:
            self._screenshots.set_interval(policy.screenshot_interval_seconds)
        if self._activity is not None:
            self._activity.set_idle_threshold(policy.idle_threshold_seconds)
        return policy


__all__ = ["PolicyService"]
