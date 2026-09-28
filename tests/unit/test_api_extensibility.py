"""The providers as *drivers*: same code, different contract.

The promise in docs/ARCHITECTURE.md § Backend-readiness is that a new endpoint
is integrated without touching the client. These tests hold the providers to
that: each one runs against a contract this build has never heard of, loaded
from a file, and must do the right thing without a line of provider code
changing.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from app.core.exceptions import AuthenticationError
from app.domain.auth.auth import AuthenticatedUser
from app.domain.auth.token_holder import AuthTokenHolder
from app.infrastructure.network.auth_adapter import (
    ApiAuthProvider,
    ApiWorkspaceProvider,
)
from app.infrastructure.network.contract import ApiContract, load_contract
from app.infrastructure.network.http_client import ApiHttpClient
from app.infrastructure.network.sync_adapter import ApiSyncProvider

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

BASE_URL = "http://backend.test/api/v1"
TOKEN = "1|token"

USER_ENVELOPE: dict[str, Any] = {
    "user": {
        "id": 1,
        "external_user_id": "1",
        "email": "employee@example.com",
        "display_name": "Employee",
        "workspace_name": "Acme Support",
    }
}


@pytest.fixture
def jpeg(tmp_path: Path) -> Path:
    path = tmp_path / "shot.jpg"
    path.write_bytes(b"\xff\xd8\xff\xe0 fake jpeg bytes")
    return path


class Recorder:
    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.responses:
            return httpx.Response(200, json={"data": {}})
        return self.responses.pop(0)

    def body(self, index: int = 0) -> dict[str, Any]:
        return json.loads(self.requests[index].content)


def contract_from(tmp_path: Path, overlay: dict[str, Any]) -> ApiContract:
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(overlay))
    return load_contract(path)


def make_sync_provider(
    handler: Callable[[httpx.Request], httpx.Response], contract: ApiContract
) -> ApiSyncProvider:
    holder = AuthTokenHolder()
    holder.set(TOKEN)
    return ApiSyncProvider(
        client=ApiHttpClient(base_url=BASE_URL, transport=httpx.MockTransport(handler)),
        token_holder=holder,
        contract=contract,
    )


class TestANewEntityNeedsNoCode:
    def test_it_syncs_to_the_declared_path_with_the_declared_fields(
        self, tmp_path: Path
    ) -> None:
        contract = contract_from(
            tmp_path,
            {
                "entities": {
                    "focus_session": {
                        "method": "PUT",
                        "path": "/v2/focus-sessions/{client_id}",
                        "summary": "Deep-work block.",
                        "body_fields": ["id", "started_at", "duration_seconds"],
                    }
                }
            },
        )
        recorder = Recorder(httpx.Response(200, json={"data": {"id": 1}}))

        result = make_sync_provider(recorder, contract).sync_item(
            entity_type="focus_session",
            entity_id=77,
            operation="CREATE",
            payload={
                "id": 77,
                "started_at": "2026-09-28T09:00:00+00:00",
                "duration_seconds": 1500,
                "local_note": "must not be sent",
            },
        )

        assert result.success is True
        request = recorder.requests[0]
        assert request.method == "PUT"
        assert str(request.url) == f"{BASE_URL}/v2/focus-sessions/77"
        assert recorder.body() == {
            "id": 77,
            "started_at": "2026-09-28T09:00:00+00:00",
            "duration_seconds": 1500,
        }

    def test_the_built_in_entities_keep_working(self, tmp_path: Path) -> None:
        contract = contract_from(
            tmp_path, {"entities": {"focus_session": {"path": "/focus/{client_id}"}}}
        )
        recorder = Recorder(httpx.Response(200, json={"data": {}}))

        make_sync_provider(recorder, contract).sync_item(
            entity_type="work_session",
            entity_id=12,
            operation="CREATE",
            payload={"id": 12, "status": "WORKING", "total_work_seconds": 0},
        )

        assert str(recorder.requests[0].url) == f"{BASE_URL}/work-sessions/12"


class TestRepointedEndpoints:
    def test_a_repointed_sync_path_is_used(self, tmp_path: Path) -> None:
        contract = contract_from(
            tmp_path, {"entities": {"break": {"path": "/v2/pauses/{client_id}"}}}
        )
        recorder = Recorder(httpx.Response(200, json={"data": {}}))

        make_sync_provider(recorder, contract).sync_item(
            entity_type="break",
            entity_id=5,
            operation="CREATE",
            payload={"id": 5, "duration_seconds": 60},
        )

        assert str(recorder.requests[0].url) == f"{BASE_URL}/v2/pauses/5"

    def test_a_repointed_identity_path_is_probed(self, tmp_path: Path) -> None:
        contract = contract_from(tmp_path, {"me": {"path": "/v2/whoami"}})
        recorder = Recorder(
            httpx.Response(200, json={"status": "ok"}),
            httpx.Response(200, json=USER_ENVELOPE),
        )

        assert (
            make_sync_provider(recorder, contract).check_connectivity().value
            == "ONLINE"
        )
        assert str(recorder.requests[1].url) == f"{BASE_URL}/v2/whoami"

    def test_a_repointed_refresh_path_is_used_on_expiry(self, tmp_path: Path) -> None:
        contract = contract_from(tmp_path, {"refresh": {"path": "/v2/auth/extend"}})
        recorder = Recorder(
            httpx.Response(401, json={"message": "expired"}),
            httpx.Response(200, json={"status": "ok"}),
            httpx.Response(200, json={"data": {}}),
        )

        result = make_sync_provider(recorder, contract).sync_item(
            entity_type="work_session",
            entity_id=1,
            operation="CREATE",
            payload={"id": 1, "status": "WORKING", "total_work_seconds": 0},
        )

        assert result.success is True
        assert [str(r.url) for r in recorder.requests][1:] == [
            f"{BASE_URL}/v2/auth/extend",
            f"{BASE_URL}/work-sessions/1",
        ]


class TestANewBinaryPayloadNeedsNoCode:
    def test_the_declared_field_name_is_used(self, tmp_path: Path, jpeg: Path) -> None:
        contract = contract_from(
            tmp_path,
            {
                "file_endpoints": {
                    "screenshot": {
                        "method": "POST",
                        "path": "/v2/screenshots/{client_id}/binary",
                        "form_fields": ["client_id", "checksum"],
                        "file_field": "blob",
                        "file_content_type": "image/png",
                    }
                }
            },
        )
        recorder = Recorder(httpx.Response(200, json={"data": {"has_file": True}}))

        result = make_sync_provider(recorder, contract).upload_screenshot(
            screenshot_id=3,
            file_path=str(jpeg),
            metadata={"id": 3, "checksum": "a" * 64, "activity_state": "ACTIVE"},
        )

        assert result.success is True
        request = recorder.requests[0]
        assert str(request.url) == f"{BASE_URL}/v2/screenshots/3/binary"
        body = request.content
        assert b'name="blob"' in body
        assert b"image/png" in body
        assert b'name="checksum"' in body


class TestUserEnvelopeMapping:
    def test_a_renamed_field_is_read_from_its_new_key(self, tmp_path: Path) -> None:
        contract = contract_from(tmp_path, {"user_fields": {"workspace_name": "team"}})
        recorder = Recorder(
            httpx.Response(
                200,
                json={
                    "token": "abc",
                    "user": {
                        "external_user_id": "1",
                        "email": "employee@example.com",
                        "display_name": "Employee",
                        "team": "Field Ops",
                    },
                },
            )
        )
        provider = ApiAuthProvider(
            client=ApiHttpClient(
                base_url=BASE_URL, transport=httpx.MockTransport(recorder)
            ),
            token_holder=AuthTokenHolder(),
            contract=contract,
        )

        session = provider.login("employee@example.com", "secret")

        assert session.user.workspace_name == "Field Ops"
        assert str(recorder.requests[0].url) == f"{BASE_URL}/auth/login"

    def test_a_missing_renamed_field_is_simply_absent(self, tmp_path: Path) -> None:
        contract = contract_from(tmp_path, {"user_fields": {"workspace_name": "team"}})
        recorder = Recorder(
            httpx.Response(
                200,
                json={
                    "token": "abc",
                    "user": {"external_user_id": "1", "email": "employee@example.com"},
                },
            )
        )
        provider = ApiAuthProvider(
            client=ApiHttpClient(
                base_url=BASE_URL, transport=httpx.MockTransport(recorder)
            ),
            token_holder=AuthTokenHolder(),
            contract=contract,
        )

        assert provider.login("a@b.c", "pw").user.workspace_name is None

    def test_login_sends_only_the_declared_fields(self, tmp_path: Path) -> None:
        """A password must reach the login endpoint and nothing else may."""
        contract = contract_from(
            tmp_path,
            {"login": {"body_fields": ["email", "password", "device_name"]}},
        )
        recorder = Recorder(httpx.Response(200, json={"token": "t", **USER_ENVELOPE}))

        ApiAuthProvider(
            client=ApiHttpClient(
                base_url=BASE_URL, transport=httpx.MockTransport(recorder)
            ),
            token_holder=AuthTokenHolder(),
            contract=contract,
        ).login("employee@example.com", "secret")

        body = recorder.body()
        assert body["email"] == "employee@example.com"
        assert body["password"] == "secret"
        assert "platform" not in body
        assert "agent_version" not in body


class TestWorkspaceEndpoint:
    def _provider(
        self, handler: Callable[[httpx.Request], httpx.Response], tmp_path: Path
    ) -> ApiWorkspaceProvider:
        contract = contract_from(tmp_path, {})
        holder = AuthTokenHolder()
        holder.set(TOKEN)
        return ApiWorkspaceProvider(
            client=ApiHttpClient(
                base_url=BASE_URL, transport=httpx.MockTransport(handler)
            ),
            token_holder=holder,
            contract=contract,
        )

    def test_it_reads_the_nested_envelope(self, tmp_path: Path) -> None:
        recorder = Recorder(
            httpx.Response(200, json={"workspace": USER_ENVELOPE["user"]})
        )

        user = self._provider(recorder, tmp_path).fetch_workspace()

        assert user is not None
        assert user.workspace_name == "Acme Support"
        assert str(recorder.requests[0].url) == f"{BASE_URL}/workspace"

    def test_it_also_accepts_a_flat_envelope(self, tmp_path: Path) -> None:
        recorder = Recorder(httpx.Response(200, json=USER_ENVELOPE["user"]))

        user = self._provider(recorder, tmp_path).fetch_workspace()

        assert user is not None
        assert user.email == "employee@example.com"

    @pytest.mark.parametrize(
        "response",
        [
            httpx.Response(500, json={"message": "boom"}),
            httpx.Response(401, json={"message": "expired"}),
            httpx.Response(200, json={"unexpected": True}),
            httpx.Response(200, text="not json"),
        ],
    )
    def test_a_failed_read_is_silent_not_an_error(
        self, response: httpx.Response, tmp_path: Path
    ) -> None:
        """A stale label is cosmetic; it must never interrupt a sync tick."""
        assert self._provider(Recorder(response), tmp_path).fetch_workspace() is None

    def test_no_token_means_no_request(self, tmp_path: Path) -> None:
        recorder = Recorder(httpx.Response(200, json=USER_ENVELOPE))
        provider = ApiWorkspaceProvider(
            client=ApiHttpClient(
                base_url=BASE_URL, transport=httpx.MockTransport(recorder)
            ),
            token_holder=AuthTokenHolder(),
        )

        assert provider.fetch_workspace() is None
        assert recorder.requests == []

    def test_a_repointed_workspace_path_is_used(self, tmp_path: Path) -> None:
        contract = contract_from(tmp_path, {"workspace": {"path": "/v2/workspace"}})
        holder = AuthTokenHolder()
        holder.set(TOKEN)
        recorder = Recorder(httpx.Response(200, json=USER_ENVELOPE["user"]))
        provider = ApiWorkspaceProvider(
            client=ApiHttpClient(
                base_url=BASE_URL, transport=httpx.MockTransport(recorder)
            ),
            token_holder=holder,
            contract=contract,
        )

        assert provider.fetch_workspace() is not None
        assert str(recorder.requests[0].url) == f"{BASE_URL}/v2/workspace"


class TestAuthProviderStillValidatesTheEnvelope:
    def test_a_200_without_a_user_is_still_an_error(self) -> None:
        recorder = Recorder(httpx.Response(200, json={"token": "abc"}))
        provider = ApiAuthProvider(
            client=ApiHttpClient(
                base_url=BASE_URL, transport=httpx.MockTransport(recorder)
            ),
            token_holder=AuthTokenHolder(),
        )

        with pytest.raises(AuthenticationError):
            provider.login("a@b.c", "pw")

    def test_the_parsed_user_is_a_domain_value(self) -> None:
        assert isinstance(
            AuthenticatedUser(external_user_id="1", email="a@b.c"), AuthenticatedUser
        )


if TYPE_CHECKING:
    from pathlib import Path
