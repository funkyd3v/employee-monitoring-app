"""Remote policy: what happens when the server says something different.

Two halves, tested separately because they fail differently:

* :class:`ApiConfigProvider` turns a wire response into a validated
  :class:`ServerPolicy`, or keeps the local defaults. It must never raise —
  a background tick has no way to recover from an exception.
* :class:`PolicyService` pushes the two values a running agent *can* adopt
  into the services that accept a setter, and says so in the log for the ones
  that only take effect at the next start.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import pytest
from app.config.settings import ServerPolicy
from app.domain.auth.token_holder import AuthTokenHolder
from app.domain.policy.provider import ConfigProvider, LocalConfigProvider
from app.infrastructure.network.config_adapter import ApiConfigProvider
from app.infrastructure.network.contract import DEFAULT_CONTRACT
from app.infrastructure.network.http_client import ApiHttpClient
from app.services.policy_service import PolicyService

if TYPE_CHECKING:
    from collections.abc import Callable

BASE_URL = "http://backend.test/api/v1"
TOKEN = "1|token"


class Recorder:
    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.responses:
            return httpx.Response(200, json={})
        return self.responses.pop(0)


def make_provider(
    handler: Callable[[httpx.Request], httpx.Response],
    fallback: ServerPolicy | None = None,
) -> ApiConfigProvider:
    holder = AuthTokenHolder()
    holder.set(TOKEN)
    return ApiConfigProvider(
        client=ApiHttpClient(base_url=BASE_URL, transport=httpx.MockTransport(handler)),
        token_holder=holder,
        endpoint=DEFAULT_CONTRACT.agent_config,
        fallback=fallback or ServerPolicy(),
    )


class TestLocalConfigProvider:
    def test_it_hands_back_what_it_was_given(self) -> None:
        policy = ServerPolicy(screenshot_interval_seconds=120)
        provider = LocalConfigProvider(policy)

        assert isinstance(provider, ConfigProvider)
        assert provider.current_policy() is policy


class TestApiConfigProvider:
    def test_it_reads_the_published_policy(self) -> None:
        recorder = Recorder(
            httpx.Response(
                200, json={"screenshot_interval_seconds": 300, "retention_days": 10}
            )
        )

        policy = make_provider(recorder).current_policy()

        assert policy.screenshot_interval_seconds == 300
        assert policy.retention_days == 10
        assert str(recorder.requests[0].url) == f"{BASE_URL}/agent/config"

    def test_unrecognised_fields_are_ignored(self) -> None:
        """A newer server must never break an older agent."""
        policy = make_provider(
            Recorder(httpx.Response(200, json={"telemetry_enabled": True}))
        ).current_policy()

        assert not hasattr(policy, "telemetry_enabled")
        assert (
            policy.screenshot_interval_seconds
            == ServerPolicy().screenshot_interval_seconds
        )

    def test_a_value_the_client_refuses_is_rejected_whole(self) -> None:
        """A 0-second interval would make the screenshot scheduler spin; the
        client's own invariant wins over the server's suggestion."""
        fallback = ServerPolicy(screenshot_interval_seconds=60)
        policy = make_provider(
            Recorder(
                httpx.Response(
                    200, json={"screenshot_interval_seconds": 0, "retention_days": 99}
                )
            ),
            fallback,
        ).current_policy()

        assert policy.screenshot_interval_seconds == 60
        assert policy.retention_days == fallback.retention_days

    @pytest.mark.parametrize(
        "response",
        [
            httpx.Response(503, json={"message": "down"}),
            httpx.Response(401, json={"message": "expired"}),
            httpx.Response(200, json=["not", "a", "dict"]),
            httpx.Response(200, text="<html>oops</html>"),
        ],
    )
    def test_any_failure_keeps_the_local_defaults(
        self, response: httpx.Response
    ) -> None:
        fallback = ServerPolicy(screenshot_interval_seconds=75)

        assert make_provider(Recorder(response), fallback).current_policy() is fallback

    def test_an_empty_object_keeps_the_local_defaults(self) -> None:
        fallback = ServerPolicy(screenshot_interval_seconds=75)

        assert (
            make_provider(
                Recorder(httpx.Response(200, json={})), fallback
            ).current_policy()
            is fallback
        )

    def test_no_token_means_no_request(self) -> None:
        recorder = Recorder(httpx.Response(200, json={"retention_days": 1}))
        provider = ApiConfigProvider(
            client=ApiHttpClient(
                base_url=BASE_URL, transport=httpx.MockTransport(recorder)
            ),
            token_holder=AuthTokenHolder(),
            endpoint=DEFAULT_CONTRACT.agent_config,
            fallback=ServerPolicy(),
        )

        assert provider.current_policy().retention_days == ServerPolicy().retention_days
        assert recorder.requests == []


class FakeScreenshotService:
    def __init__(self) -> None:
        self.interval: int | None = None

    def set_interval(self, seconds: int) -> None:
        self.interval = seconds


class FakeActivityService:
    def __init__(self) -> None:
        self.threshold: int | None = None

    def set_idle_threshold(self, seconds: int) -> None:
        self.threshold = seconds


class StubConfigProvider(ConfigProvider):
    def __init__(self, policy: ServerPolicy) -> None:
        self.policy = policy
        self.calls = 0

    def current_policy(self) -> ServerPolicy:
        self.calls += 1
        return self.policy


class TestPolicyService:
    def _service(
        self, incoming: ServerPolicy
    ) -> tuple[
        PolicyService, StubConfigProvider, FakeScreenshotService, FakeActivityService
    ]:
        provider = StubConfigProvider(incoming)
        screenshots = FakeScreenshotService()
        activity = FakeActivityService()
        return (
            PolicyService(
                config_provider=provider,
                settings=ServerPolicy(),
                screenshot_service=screenshots,  # type: ignore[arg-type]
                activity_service=activity,  # type: ignore[arg-type]
            ),
            provider,
            screenshots,
            activity,
        )

    def test_a_changed_live_value_reaches_its_service(self) -> None:
        service, _, screenshots, activity = self._service(
            ServerPolicy(screenshot_interval_seconds=300, idle_threshold_seconds=90)
        )

        service.refresh()

        assert screenshots.interval == 300
        assert activity.threshold == 90

    def test_an_unchanged_policy_touches_nothing(self) -> None:
        service, provider, screenshots, activity = self._service(ServerPolicy())

        service.refresh()

        assert provider.calls == 1
        assert screenshots.interval is None
        assert activity.threshold is None

    def test_a_provider_that_raises_is_survivable(self) -> None:
        class Exploding(ConfigProvider):
            def current_policy(self) -> ServerPolicy:
                raise RuntimeError("network gone")

        service = PolicyService(
            config_provider=Exploding(),
            settings=ServerPolicy(screenshot_interval_seconds=60),
        )

        with pytest.raises(RuntimeError):
            service.refresh()

        # The tick catches this; the service itself keeps its last policy.
        assert service.policy.screenshot_interval_seconds == 60

    def test_apply_adopts_a_policy_immediately(self) -> None:
        service, _, screenshots, activity = self._service(ServerPolicy())

        service.apply(
            ServerPolicy(screenshot_interval_seconds=90, idle_threshold_seconds=5)
        )

        assert screenshots.interval == 90
        assert activity.threshold == 5
        assert service.policy.screenshot_interval_seconds == 90

    def test_it_works_without_the_optional_services(self) -> None:
        service = PolicyService(
            config_provider=StubConfigProvider(
                ServerPolicy(screenshot_interval_seconds=120)
            ),
            settings=ServerPolicy(),
        )

        assert service.refresh().screenshot_interval_seconds == 120

    def test_the_effective_policy_is_observable(self) -> None:
        service, _, _, _ = self._service(ServerPolicy(retention_days=3))

        assert service.refresh().retention_days == 3


class TestConfigProviderContract:
    def test_both_implementations_satisfy_the_interface(self) -> None:
        for implementation in (
            LocalConfigProvider(ServerPolicy()),
            make_provider(Recorder()),
        ):
            assert isinstance(implementation, ConfigProvider)

    def test_the_provider_names_the_endpoint_it_reads(self) -> None:
        assert make_provider(Recorder()).endpoint_path == "/agent/config"

    def test_typing_of_a_policy_payload(self) -> None:
        payload: dict[str, Any] = {"screenshot_interval_seconds": 90}
        assert ServerPolicy.model_validate(payload).screenshot_interval_seconds == 90
