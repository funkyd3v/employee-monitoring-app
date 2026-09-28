"""Unit tests for the API HTTP client's failure classification.

Connectivity model (docs/ENGINEERING_RULES.md §Connectivity model): the whole
point of this layer is that the agent can tell "backend down" apart from
"token rejected" apart from "you sent nonsense", because each one demands a
different reaction from the outbox.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest
from app.domain.sync.provider import ConnectivityState
from app.infrastructure.network.http_client import (
    ApiAuthError,
    ApiHttpClient,
    ApiProtocolError,
    ApiRejectedError,
    ApiUnavailableError,
    classify_response,
)

BASE_URL = "http://backend.test/api/v1"


def make_client(handler: Any) -> ApiHttpClient:
    return ApiHttpClient(
        base_url=BASE_URL,
        transport=httpx.MockTransport(handler),
    )


def _response(status: int, payload: Any = None) -> httpx.Response:
    body = json.dumps(payload if payload is not None else {"message": "nope"})
    return httpx.Response(status, content=body.encode())


class TestClassifyResponse:
    def test_success_is_not_a_failure(self) -> None:
        assert classify_response(httpx.Response(200, json={"ok": True})) is None

    def test_created_is_not_a_failure(self) -> None:
        assert classify_response(httpx.Response(201)) is None

    @pytest.mark.parametrize("status", [401, 403])
    def test_auth_failures_are_not_retryable(self, status: int) -> None:
        error = classify_response(_response(status))
        assert isinstance(error, ApiAuthError)
        assert error.connectivity is ConnectivityState.AUTHENTICATION_REQUIRED
        assert error.retryable is False

    @pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
    def test_transient_statuses_are_retryable(self, status: int) -> None:
        error = classify_response(_response(status))
        assert isinstance(error, ApiUnavailableError)
        assert error.connectivity is ConnectivityState.BACKEND_UNAVAILABLE
        assert error.retryable is True

    @pytest.mark.parametrize("status", [400, 404, 409, 413, 422])
    def test_client_errors_are_not_retryable(self, status: int) -> None:
        """A replayed identical request would be refused identically."""
        error = classify_response(_response(status))
        assert isinstance(error, ApiRejectedError)
        assert error.retryable is False

    def test_sync_result_projection_keeps_the_classification(self) -> None:
        result = classify_response(_response(422)).to_sync_result()  # type: ignore[union-attr]
        assert result.success is False
        assert result.retryable is False
        assert result.connectivity is ConnectivityState.ONLINE


class TestRequestClassification:
    def test_connection_error_is_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        with pytest.raises(ApiUnavailableError) as excinfo:
            make_client(handler).request("GET", "/me")
        assert excinfo.value.retryable is True

    def test_read_timeout_is_unavailable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("too slow", request=request)

        with pytest.raises(ApiUnavailableError):
            make_client(handler).request("GET", "/me")

    def test_error_message_never_leaks_the_token(self, caplog) -> None:
        """The bearer token is sent in a header, never in a log or exception."""

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["authorization"] == "Bearer super-secret-token"
            return _response(500)

        with (
            caplog.at_level(logging.DEBUG, logger="employee_monitoring_agent"),
            pytest.raises(ApiUnavailableError) as excinfo,
        ):
            make_client(handler).request("GET", "/me", token="super-secret-token")

        assert "super-secret-token" not in str(excinfo.value)
        assert all(
            "super-secret-token" not in record.getMessage() for record in caplog.records
        )


class TestJsonDecoding:
    def test_non_json_success_is_a_protocol_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"<html>captive portal</html>")

        client = make_client(handler)
        response = client.request("GET", "/me")

        with pytest.raises(ApiProtocolError) as excinfo:
            client.json(response, context="me")
        assert excinfo.value.retryable is True

    def test_json_array_body_is_a_protocol_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[1, 2, 3])

        client = make_client(handler)
        response = client.request("GET", "/me")

        with pytest.raises(ApiProtocolError):
            client.json(response, context="me")


class TestReachability:
    def test_reachable_on_200(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "ok"})

        assert make_client(handler).is_reachable() is True

    def test_unreachable_on_connection_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host", request=request)

        assert make_client(handler).is_reachable() is False

    def test_error_status_is_not_reachable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        assert make_client(handler).is_reachable() is False


class TestConstruction:
    def test_empty_base_url_is_refused(self) -> None:
        with pytest.raises(ValueError, match="base_url"):
            ApiHttpClient(base_url="")

    def test_trailing_slash_is_normalised(self) -> None:
        assert ApiHttpClient(base_url="http://x/api/v1/").base_url == "http://x/api/v1"

    def test_close_is_idempotent(self) -> None:
        client = make_client(lambda _request: httpx.Response(200))
        client.close()
        client.close()
