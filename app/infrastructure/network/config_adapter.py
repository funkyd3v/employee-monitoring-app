"""``ApiConfigProvider`` — read the operator's policy over HTTP.

The interface and the local implementation live in
:mod:`app.domain.policy.provider`; only the network half belongs here, keeping
the layering rule intact (docs/PROJECT_STRUCTURE.md): the domain declares what
it needs, infrastructure decides how it arrives.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.config.settings import ServerPolicy
from app.core.logging import get_logger
from app.domain.policy.provider import ConfigProvider

if TYPE_CHECKING:
    from app.domain.auth.token_holder import AuthTokenHolder
    from app.infrastructure.network.contract import Endpoint
    from app.infrastructure.network.http_client import ApiHttpClient

_logger = get_logger("policy.provider")


class ApiConfigProvider(ConfigProvider):
    """``mode: "api"``: read the policy the backend publishes.

    Falls back to the local defaults on any failure, and ignores individual
    fields it does not recognise so a server can add a policy key before the
    client understands it — the usual compatibility rule: a newer server must
    never be able to break an older agent.
    """

    def __init__(
        self,
        *,
        client: ApiHttpClient,
        token_holder: AuthTokenHolder,
        endpoint: Endpoint,
        fallback: ServerPolicy,
    ) -> None:
        self._client = client
        self._token_holder = token_holder
        self._endpoint = endpoint
        self._fallback = fallback

    def current_policy(self) -> ServerPolicy:
        token = self._token_holder.get()
        if token is None:
            return self._fallback

        try:
            response = self._client.request(
                self._endpoint.method, self._endpoint.path, token=token
            )
            payload = self._client.json(response, context="agent config")
        except Exception as exc:  # a policy read must never break a tick
            _logger.debug("remote policy unavailable, keeping local defaults: %s", exc)
            return self._fallback

        if not isinstance(payload, dict):
            return self._fallback

        known = set(ServerPolicy.model_fields)
        values = {key: value for key, value in payload.items() if key in known}

        if not values:
            _logger.debug("remote policy carried no recognised fields")
            return self._fallback

        merged = self._fallback.model_copy(update=values)
        # `model_copy` does not validate: re-validate so a server cannot push a
        # value the client's own invariants forbid (a 0-second screenshot
        # interval, say) by going around them.
        try:
            return ServerPolicy.model_validate(merged.model_dump())
        except Exception as exc:  # keep the local defaults
            _logger.warning("remote policy rejected (%s); keeping local defaults", exc)
            return self._fallback

    @property
    def endpoint_path(self) -> str:
        return self._endpoint.path


__all__ = ["ApiConfigProvider"]
