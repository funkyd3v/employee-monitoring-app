"""Unit tests for :class:`ApiAuthProvider`.

The provider is the only place a password ever exists in the client, so these
tests are mostly about what it must *not* do: log the secret, accept bad
credentials, or leave a stale token in the holder.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from app.core.exceptions import AuthenticationError
from app.domain.auth.token_holder import AuthTokenHolder
from app.infrastructure.network.auth_adapter import ApiAuthProvider
from app.infrastructure.network.http_client import ApiHttpClient

BASE_URL = "http://backend.test/api/v1"
EMAIL = "employee@example.com"
PASSWORD = "s3cret!"
TOKEN = "1|abcdefghijklmnopqrstuvwxyz"

LOGIN_BODY = {
    "token": TOKEN,
    "token_type": "Bearer",
    "expires_at": "2026-10-28T09:00:00+00:00",
    "user": {
        "id": 1,
        "external_user_id": "1",
        "email": EMAIL,
        "display_name": "Jane Doe",
        "workspace_name": "Engineering",
    },
}


def make_provider(
    handler: Any, holder: AuthTokenHolder | None = None
) -> ApiAuthProvider:
    client = ApiHttpClient(
        base_url=BASE_URL,
        transport=httpx.MockTransport(handler),
    )
    return ApiAuthProvider(client=client, token_holder=holder or AuthTokenHolder())


def json_handler(
    responses: list[httpx.Response], recorder: list[httpx.Request] | None = None
) -> Any:
    """Serve ``responses`` in order, recording every request."""
    remaining = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        if recorder is not None:
            recorder.append(request)
        return remaining.pop(0) if remaining else httpx.Response(500)

    return handler


class TestLogin:
    def test_returns_the_user_and_publishes_the_token(self) -> None:
        holder = AuthTokenHolder()
        provider = make_provider(
            json_handler([httpx.Response(200, json=LOGIN_BODY)]), holder
        )

        session = provider.login(EMAIL, PASSWORD)

        assert session.token == TOKEN
        assert session.user.email == EMAIL
        assert session.user.external_user_id == "1"
        assert session.user.display_name == "Jane Doe"
        assert session.user.workspace_name == "Engineering"
        assert holder.get() == TOKEN

    def test_sends_credentials_to_the_documented_endpoint(self) -> None:
        recorder: list[httpx.Request] = []
        provider = make_provider(
            json_handler([httpx.Response(200, json=LOGIN_BODY)], recorder)
        )

        provider.login(EMAIL, PASSWORD)

        assert len(recorder) == 1
        request = recorder[0]
        assert request.method == "POST"
        assert str(request.url) == f"{BASE_URL}/auth/login"
        body = json.loads(request.content)
        assert body["email"] == EMAIL
        assert body["password"] == PASSWORD

    def test_reports_device_context(self) -> None:
        """Operators need to tell two machines apart; all fields are optional."""
        recorder: list[httpx.Request] = []
        provider = make_provider(
            json_handler([httpx.Response(200, json=LOGIN_BODY)], recorder)
        )

        provider.login(EMAIL, PASSWORD)

        body = json.loads(recorder[0].content)
        assert "device_name" in body
        assert body["platform"]
        assert body["agent_version"]

    @pytest.mark.parametrize("status", [401, 403, 422])
    def test_bad_credentials_raise_authentication_error(self, status: int) -> None:
        provider = make_provider(
            json_handler([httpx.Response(status, json={"message": "nope"})])
        )

        with pytest.raises(AuthenticationError):
            provider.login(EMAIL, PASSWORD)

    def test_unreachable_backend_raises_authentication_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        with pytest.raises(AuthenticationError, match="could not reach"):
            make_provider(handler).login(EMAIL, PASSWORD)

    def test_malformed_success_body_raises(self) -> None:
        provider = make_provider(json_handler([httpx.Response(200, json={"ok": True})]))

        with pytest.raises(AuthenticationError, match="unexpected login response"):
            provider.login(EMAIL, PASSWORD)

    def test_token_missing_from_body_raises(self) -> None:
        body = {k: v for k, v in LOGIN_BODY.items() if k != "token"}
        provider = make_provider(json_handler([httpx.Response(200, json=body)]))

        with pytest.raises(AuthenticationError, match="unexpected login response"):
            provider.login(EMAIL, PASSWORD)

    def test_never_logs_the_password(self, caplog) -> None:
        import logging

        with caplog.at_level(logging.DEBUG, logger="employee_monitoring_agent"):
            make_provider(json_handler([httpx.Response(200, json=LOGIN_BODY)])).login(
                EMAIL, PASSWORD
            )

        assert all(PASSWORD not in r.getMessage() for r in caplog.records)
        assert all(TOKEN not in r.getMessage() for r in caplog.records)


class TestRefresh:
    def test_valid_token_returns_the_same_token(self) -> None:
        """The client re-reads one long-lived credential, so no rotation."""
        recorder: list[httpx.Request] = []
        holder = AuthTokenHolder()
        holder.set(TOKEN)
        provider = make_provider(
            json_handler([httpx.Response(200, json=LOGIN_BODY)], recorder), holder
        )

        session = provider.refresh(TOKEN)

        assert session.token == TOKEN
        assert session.user.email == EMAIL
        assert recorder[0].headers["authorization"] == f"Bearer {TOKEN}"
        assert holder.get() == TOKEN

    def test_rejected_token_raises_and_leaves_holder_untouched(self) -> None:
        """AuthService decides what an invalid token means; the holder is not
        cleared here so a transient failure cannot silently sign the user out."""
        holder = AuthTokenHolder()
        holder.set(TOKEN)
        provider = make_provider(
            json_handler([httpx.Response(401, json={"message": "Unauthenticated."})]),
            holder,
        )

        with pytest.raises(AuthenticationError, match="no longer valid"):
            provider.refresh(TOKEN)

        assert holder.get() == TOKEN


class TestLogout:
    def test_revokes_and_clears_the_holder(self) -> None:
        recorder: list[httpx.Request] = []
        holder = AuthTokenHolder()
        holder.set(TOKEN)
        provider = make_provider(
            json_handler(
                [httpx.Response(200, json={"status": "logged_out"})], recorder
            ),
            holder,
        )

        provider.logout(TOKEN)

        assert recorder[0].method == "POST"
        assert str(recorder[0].url) == f"{BASE_URL}/auth/logout"
        assert holder.get() is None

    def test_backend_failure_still_clears_the_holder(self) -> None:
        """The employee must always be able to log out, online or not."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        holder = AuthTokenHolder()
        holder.set(TOKEN)

        make_provider(handler, holder).logout(TOKEN)  # must not raise

        assert holder.get() is None
