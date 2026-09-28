"""Unit tests for :class:`ApiSyncProvider`.

Two things are being pinned down here:

* **Routing** — each outbox entity type lands on the endpoint documented in
  ``docs/API_CONTRACT.md``, carrying the agent's local row id in both the path
  and the body so a server/client contract drift is a 422, not a mis-filed row.
* **Reaction** — every wire outcome maps to the right
  :class:`~app.domain.sync.provider.SyncResult`, which is the only signal the
  sync service uses to decide whether it may delete local data.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from app.domain.auth.token_holder import AuthTokenHolder
from app.domain.sync.provider import ConnectivityState
from app.infrastructure.network.http_client import ApiHttpClient
from app.infrastructure.network.sync_adapter import ApiSyncProvider

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

BASE_URL = "http://backend.test/api/v1"
TOKEN = "1|token"

WORK_SESSION_PAYLOAD = {
    "id": 12,
    "user_id": 1,
    "started_at": "2026-09-28T09:00:00+00:00",
    "ended_at": None,
    "status": "WORKING",
    "total_work_seconds": 0,
}

BREAK_PAYLOAD = {
    "id": 5,
    "session_id": 12,
    "started_at": "2026-09-28T10:00:00+00:00",
    "ended_at": None,
    "duration_seconds": 0,
}

PERIOD_PAYLOAD = {
    "id": 7,
    "session_id": 12,
    "started_at": "2026-09-28T09:00:00+00:00",
    "ended_at": None,
    "state": "ACTIVE",
    "duration_seconds": 0,
}

SCREENSHOT_METADATA = {
    "id": 3,
    "session_id": 12,
    "captured_at": "2026-09-28T09:31:00+00:00",
    "activity_state": "ACTIVE",
    "file_size": 2536,
    "checksum": "8" * 64,
    "sync_status": "PENDING",
}


def make_provider(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    token: str | None = TOKEN,
) -> ApiSyncProvider:
    holder = AuthTokenHolder()
    if token is not None:
        holder.set(token)
    return ApiSyncProvider(
        client=ApiHttpClient(base_url=BASE_URL, transport=httpx.MockTransport(handler)),
        token_holder=holder,
    )


class Recorder:
    """Serves a queue of responses and keeps the requests it was given."""

    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.responses:
            return httpx.Response(500, json={"message": "no scripted response"})
        return self.responses.pop(0)

    def body(self, index: int = 0) -> dict[str, Any]:
        return json.loads(self.requests[index].content)


class TestConnectivity:
    def test_online_when_health_and_identity_both_answer(self) -> None:
        recorder = Recorder(
            httpx.Response(200, json={"status": "ok"}),
            httpx.Response(200, json={"user": {"id": 1, "email": "e@x.test"}}),
        )
        assert make_provider(recorder).check_connectivity() is ConnectivityState.ONLINE

    def test_unavailable_when_backend_cannot_be_reached(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        assert (
            make_provider(handler).check_connectivity()
            is ConnectivityState.BACKEND_UNAVAILABLE
        )

    def test_auth_required_when_no_token_is_held(self) -> None:
        """A reachable server with no credential is not "online" — otherwise
        the outbox would retry every item against a server that will 401."""
        recorder = Recorder(httpx.Response(200, json={"status": "ok"}))
        provider = make_provider(recorder, token=None)

        assert (
            provider.check_connectivity() is ConnectivityState.AUTHENTICATION_REQUIRED
        )
        # The identity probe must not even be attempted without a token.
        assert len(recorder.requests) == 1
        assert str(recorder.requests[0].url).endswith("/health")

    def test_auth_required_when_token_is_rejected(self) -> None:
        recorder = Recorder(
            httpx.Response(200, json={"status": "ok"}),
            httpx.Response(401, json={"message": "Unauthenticated."}),
        )
        assert (
            make_provider(recorder).check_connectivity()
            is ConnectivityState.AUTHENTICATION_REQUIRED
        )

    def test_server_error_is_unavailable_not_online(self) -> None:
        recorder = Recorder(httpx.Response(503))
        assert (
            make_provider(recorder).check_connectivity()
            is ConnectivityState.BACKEND_UNAVAILABLE
        )


class TestSyncItemRouting:
    @pytest.mark.parametrize(
        ("entity_type", "entity_id", "expected_path"),
        [
            ("work_session", 12, "/work-sessions/12"),
            ("break", 5, "/breaks/5"),
            ("activity_period", 7, "/activity-periods/7"),
            ("screenshot", 3, "/screenshots/3"),
        ],
    )
    def test_each_entity_type_has_its_own_endpoint(
        self, entity_type: str, entity_id: int, expected_path: str
    ) -> None:
        recorder = Recorder(httpx.Response(200, json={"data": {"id": 1}}))
        payloads = {
            "work_session": WORK_SESSION_PAYLOAD,
            "break": BREAK_PAYLOAD,
            "activity_period": PERIOD_PAYLOAD,
            "screenshot": SCREENSHOT_METADATA,
        }

        result = make_provider(recorder).sync_item(
            entity_type=entity_type,
            entity_id=entity_id,
            operation="CREATE",
            payload=payloads[entity_type],
        )

        assert result.success is True
        assert str(recorder.requests[0].url) == f"{BASE_URL}{expected_path}"
        assert recorder.requests[0].method == "PUT"
        assert recorder.requests[0].headers["authorization"] == f"Bearer {TOKEN}"

    def test_local_id_travels_in_the_body_too(self) -> None:
        """Both sides must agree on which row this is."""
        recorder = Recorder(httpx.Response(200, json={"data": {}}))

        make_provider(recorder).sync_item(
            entity_type="work_session",
            entity_id=12,
            operation="UPDATE",
            payload=WORK_SESSION_PAYLOAD,
        )

        assert recorder.body()["id"] == 12

    def test_create_and_update_send_the_same_request(self) -> None:
        """That equality is what makes a replayed upload safe."""
        first = Recorder(httpx.Response(200, json={"data": {}}))
        second = Recorder(httpx.Response(200, json={"data": {}}))
        provider_a = make_provider(first)
        provider_b = make_provider(second)

        provider_a.sync_item(
            entity_type="work_session",
            entity_id=12,
            operation="CREATE",
            payload=WORK_SESSION_PAYLOAD,
        )
        provider_b.sync_item(
            entity_type="work_session",
            entity_id=12,
            operation="UPDATE",
            payload=WORK_SESSION_PAYLOAD,
        )

        assert first.body() == second.body()

    def test_unknown_entity_type_is_not_retryable(self) -> None:
        """Refusing loudly beats a silent drop the outbox would keep retrying."""
        recorder = Recorder()
        result = make_provider(recorder).sync_item(
            entity_type="keystrokes", entity_id=1, operation="CREATE", payload={"id": 1}
        )

        assert result.success is False
        assert result.retryable is False
        assert recorder.requests == []

    def test_empty_payload_is_not_retryable(self) -> None:
        result = make_provider(Recorder()).sync_item(
            entity_type="work_session", entity_id=12, operation="CREATE", payload={}
        )

        assert result.success is False
        assert result.retryable is False

    @pytest.mark.parametrize("status", [400, 404, 409, 422])
    def test_rejected_payload_is_not_retryable(self, status: int) -> None:
        """A replayed identical request would be refused identically."""
        recorder = Recorder(httpx.Response(status, json={"message": "invalid"}))
        result = make_provider(recorder).sync_item(
            entity_type="work_session",
            entity_id=12,
            operation="CREATE",
            payload=WORK_SESSION_PAYLOAD,
        )

        assert result.success is False
        assert result.retryable is False
        assert result.connectivity is ConnectivityState.ONLINE

    @pytest.mark.parametrize("status", [500, 502, 503, 429])
    def test_transient_server_error_is_retryable(self, status: int) -> None:
        recorder = Recorder(httpx.Response(status, json={"message": "later"}))
        result = make_provider(recorder).sync_item(
            entity_type="work_session",
            entity_id=12,
            operation="CREATE",
            payload=WORK_SESSION_PAYLOAD,
        )

        assert result.success is False
        assert result.retryable is True
        assert result.connectivity is ConnectivityState.BACKEND_UNAVAILABLE

    def test_connection_error_is_retryable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        result = make_provider(handler).sync_item(
            entity_type="work_session",
            entity_id=12,
            operation="CREATE",
            payload=WORK_SESSION_PAYLOAD,
        )

        assert result.retryable is True
        assert result.connectivity is ConnectivityState.BACKEND_UNAVAILABLE

    def test_missing_token_is_auth_required(self) -> None:
        recorder = Recorder()
        result = make_provider(recorder, token=None).sync_item(
            entity_type="work_session",
            entity_id=12,
            operation="CREATE",
            payload=WORK_SESSION_PAYLOAD,
        )

        assert result.success is False
        assert result.retryable is False
        assert result.connectivity is ConnectivityState.AUTHENTICATION_REQUIRED


class TestTokenRefreshOnExpiry:
    def test_expired_token_is_refreshed_once_and_replayed(self) -> None:
        """A token can expire mid-shift; the employee should not notice."""
        recorder = Recorder(
            httpx.Response(401, json={"message": "Unauthenticated."}),
            httpx.Response(200, json={"token": TOKEN, "user": {"id": 1}}),
            httpx.Response(200, json={"data": {}}),
        )

        result = make_provider(recorder).sync_item(
            entity_type="work_session",
            entity_id=12,
            operation="CREATE",
            payload=WORK_SESSION_PAYLOAD,
        )

        assert result.success is True
        assert [r.method for r in recorder.requests] == ["PUT", "POST", "PUT"]
        assert str(recorder.requests[1].url) == f"{BASE_URL}/auth/refresh"

    def test_failed_refresh_reports_auth_required(self) -> None:
        recorder = Recorder(
            httpx.Response(401, json={"message": "Unauthenticated."}),
            httpx.Response(401, json={"message": "Unauthenticated."}),
        )

        result = make_provider(recorder).sync_item(
            entity_type="work_session",
            entity_id=12,
            operation="CREATE",
            payload=WORK_SESSION_PAYLOAD,
        )

        assert result.success is False
        assert result.connectivity is ConnectivityState.AUTHENTICATION_REQUIRED
        assert len(recorder.requests) == 2  # give up rather than loop


class TestScreenshotUpload:
    @pytest.fixture
    def jpeg(self, tmp_path: Path) -> Path:
        path = tmp_path / "shot.jpg"
        path.write_bytes(b"\xff\xd8\xff\xe0 fake jpeg bytes")
        return path

    def test_uploads_binary_with_the_documented_form_fields(self, jpeg: Path) -> None:
        recorder = Recorder(httpx.Response(200, json={"data": {"has_file": True}}))

        result = make_provider(recorder).upload_screenshot(
            screenshot_id=3, file_path=str(jpeg), metadata=SCREENSHOT_METADATA
        )

        assert result.success is True
        request = recorder.requests[0]
        assert request.method == "POST"
        assert str(request.url) == f"{BASE_URL}/screenshots/3/file"
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        body = request.content
        assert b'name="checksum"' in body
        assert b"8" * 64 in body
        assert b'name="activity_state"' in body
        assert b"ACTIVE" in body
        assert b"fake jpeg bytes" in body

    def test_local_bookkeeping_fields_are_not_sent(self, jpeg: Path) -> None:
        """``sync_status`` is the agent's own state; the server has no use for it."""
        recorder = Recorder(httpx.Response(200, json={}))
        make_provider(recorder).upload_screenshot(
            screenshot_id=3, file_path=str(jpeg), metadata=SCREENSHOT_METADATA
        )

        assert b"sync_status" not in recorder.requests[0].content

    def test_missing_file_is_not_retryable(self, tmp_path: Path) -> None:
        recorder = Recorder()
        result = make_provider(recorder).upload_screenshot(
            screenshot_id=3,
            file_path=str(tmp_path / "gone.jpg"),
            metadata=SCREENSHOT_METADATA,
        )

        assert result.success is False
        assert result.retryable is False
        assert recorder.requests == []

    def test_rejected_upload_keeps_local_data(self, jpeg: Path) -> None:
        """A checksum mismatch is the server refusing corrupt bytes; the local
        file must survive so the employee is not left with nothing."""
        recorder = Recorder(httpx.Response(422, json={"message": "checksum"}))

        result = make_provider(recorder).upload_screenshot(
            screenshot_id=3, file_path=str(jpeg), metadata=SCREENSHOT_METADATA
        )

        assert result.success is False
        assert result.retryable is False
        assert jpeg.exists()

    def test_server_error_is_retryable(self, jpeg: Path) -> None:
        recorder = Recorder(httpx.Response(503, json={}))
        result = make_provider(recorder).upload_screenshot(
            screenshot_id=3, file_path=str(jpeg), metadata=SCREENSHOT_METADATA
        )

        assert result.retryable is True
        assert result.connectivity is ConnectivityState.BACKEND_UNAVAILABLE

    def test_no_token_is_auth_required(self, jpeg: Path) -> None:
        result = make_provider(Recorder(), token=None).upload_screenshot(
            screenshot_id=3, file_path=str(jpeg), metadata=SCREENSHOT_METADATA
        )

        assert result.connectivity is ConnectivityState.AUTHENTICATION_REQUIRED

    def test_expired_token_is_refreshed_once(self, jpeg: Path) -> None:
        recorder = Recorder(
            httpx.Response(401, json={}),
            httpx.Response(200, json={}),
            httpx.Response(200, json={"data": {}}),
        )

        result = make_provider(recorder).upload_screenshot(
            screenshot_id=3, file_path=str(jpeg), metadata=SCREENSHOT_METADATA
        )

        assert result.success is True
        assert len(recorder.requests) == 3


class TestSyncBatch:
    def test_default_batching_fans_out_one_request_per_item(self) -> None:
        """No bulk endpoint: the outbox needs a binary answer per item before
        it may delete anything."""
        recorder = Recorder(httpx.Response(200, json={}), httpx.Response(200, json={}))

        results = make_provider(recorder).sync_batch(
            [
                {
                    "entity_type": "work_session",
                    "entity_id": 12,
                    "operation": "CREATE",
                    "payload": WORK_SESSION_PAYLOAD,
                },
                {
                    "entity_type": "break",
                    "entity_id": 5,
                    "operation": "CREATE",
                    "payload": BREAK_PAYLOAD,
                },
            ]
        )

        assert [r.success for r in results] == [True, True]
        assert [str(r.url) for r in recorder.requests] == [
            f"{BASE_URL}/work-sessions/12",
            f"{BASE_URL}/breaks/5",
        ]


class TestTimestampContract:
    def test_metadata_preserves_the_agents_utc_offset_format(self) -> None:
        """Timestamps are ISO-8601 UTC with an offset; the server parses them
        as-is, so the client must not reformat them."""
        recorder = Recorder(httpx.Response(200, json={}))
        captured_at = datetime(2026, 9, 28, 9, 31, tzinfo=UTC).isoformat()

        make_provider(recorder).sync_item(
            entity_type="screenshot",
            entity_id=3,
            operation="CREATE",
            payload={**SCREENSHOT_METADATA, "captured_at": captured_at},
        )

        assert recorder.body()["captured_at"] == captured_at
