"""The wire contract, as data.

These tests are the point of the module: a contract is data, so the claims that
matter — that a new entity needs no code, that a differently-shaped backend
can be integrated from a file, and that a broken file is a startup error — are
all assertable without a server.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from app.core.exceptions import ConfigurationError
from app.infrastructure.network.contract import (
    DEFAULT_CONTRACT,
    ApiContract,
    Endpoint,
    load_contract,
)

PAYLOAD: dict[str, Any] = {
    "id": 7,
    "started_at": "2026-09-28T09:00:00+00:00",
    "ended_at": None,
    "status": "WORKING",
    "total_work_seconds": 0,
    "sync_status": "PENDING",
}


class TestEndpointDeclaration:
    def test_the_path_takes_the_local_row_id(self) -> None:
        endpoint = DEFAULT_CONTRACT.entity("work_session")
        assert endpoint is not None
        assert endpoint.url(client_id=42) == "/work-sessions/42"

    def test_only_declared_fields_are_sent(self) -> None:
        """A local bookkeeping column must never reach the server."""
        endpoint = DEFAULT_CONTRACT.entity("work_session")
        assert endpoint is not None
        body = endpoint.body(PAYLOAD, client_id=7)

        assert body == {
            "id": 7,
            "started_at": "2026-09-28T09:00:00+00:00",
            "ended_at": None,
            "status": "WORKING",
            "total_work_seconds": 0,
        }
        assert "sync_status" not in body

    def test_the_local_id_always_travels_in_the_body_too(self) -> None:
        endpoint = Endpoint(
            name="x",
            method="PUT",
            path="/x/{client_id}",
            summary="",
            body_fields=("a",),
        )
        assert endpoint.body({"a": 1}, client_id=99) == {"a": 1, "id": 99}

    def test_an_absent_local_id_falls_back_to_the_queue_id(self) -> None:
        endpoint = DEFAULT_CONTRACT.entity("screenshot")
        assert endpoint is not None
        assert endpoint.body({"captured_at": "now"}, client_id=5)["id"] == 5

    def test_a_no_body_endpoint_sends_nothing(self) -> None:
        endpoint = DEFAULT_CONTRACT.entity("user")
        assert endpoint.sends_body is False
        assert endpoint.body(PAYLOAD, client_id=1) == {}

    def test_a_passthrough_endpoint_sends_everything(self) -> None:
        endpoint = Endpoint(
            name="x", method="PUT", path="/x", summary="", body_fields=None
        )
        assert endpoint.body(PAYLOAD, client_id=1) == PAYLOAD

    def test_form_fields_rename_and_drop_absent_values(self) -> None:
        """The upload's `client_id` is the local `id` — declared, not assumed."""
        endpoint = DEFAULT_CONTRACT.file_endpoint("screenshot")
        assert endpoint is not None
        form = endpoint.form({"id": 3, "session_id": None, "checksum": "abc"})

        assert form == {"client_id": "3", "checksum": "abc"}
        assert all(isinstance(value, str) for value in form.values())

    def test_the_screenshot_upload_declares_its_file_field(self) -> None:
        endpoint = DEFAULT_CONTRACT.file_endpoint("screenshot")
        assert endpoint is not None
        assert endpoint.uploads_file is True
        assert endpoint.file_field == "file"
        assert endpoint.file_content_type == "image/jpeg"


class TestDefaultContract:
    def test_it_covers_every_outbox_entity_type(self) -> None:
        # The domain defines these; a missing declaration would turn a queued
        # row into a permanent non-retryable failure.
        assert DEFAULT_CONTRACT.known_entities() == (
            "activity_period",
            "break",
            "screenshot",
            "user",
            "work_session",
        )

    def test_every_sync_entity_is_an_upsert_or_a_read(self) -> None:
        for name, endpoint in DEFAULT_CONTRACT.entities.items():
            assert endpoint.method in {"GET", "PUT", "POST"}, name
            assert endpoint.summary, f"{name} has no summary"

    def test_nothing_but_health_and_login_are_public(self) -> None:
        public = [
            endpoint.name
            for endpoint in (
                DEFAULT_CONTRACT.health,
                DEFAULT_CONTRACT.login,
                DEFAULT_CONTRACT.refresh,
                DEFAULT_CONTRACT.logout,
                DEFAULT_CONTRACT.identity,
                DEFAULT_CONTRACT.workspace,
                DEFAULT_CONTRACT.agent_config,
            )
        ]
        assert public[0] == "health"
        assert DEFAULT_CONTRACT.health.authenticated is False
        assert DEFAULT_CONTRACT.login.authenticated is False
        assert all(
            endpoint.authenticated
            for endpoint in (
                DEFAULT_CONTRACT.refresh,
                DEFAULT_CONTRACT.logout,
                DEFAULT_CONTRACT.identity,
                DEFAULT_CONTRACT.workspace,
                DEFAULT_CONTRACT.agent_config,
            )
        )

    def test_the_describe_line_names_the_surface(self) -> None:
        described = DEFAULT_CONTRACT.describe()
        assert "5 sync entities" in described
        assert "/me" in described


class TestUserFieldMapping:
    def test_fields_map_to_themselves_by_default(self) -> None:
        assert DEFAULT_CONTRACT.user_key("email") == "email"
        assert DEFAULT_CONTRACT.user_key("workspace_name") == "workspace_name"

    def test_a_mapping_can_rename_a_field(self, tmp_path: Path) -> None:
        contract = _contract_from({"user_fields": {"workspace_name": "team"}}, tmp_path)
        assert contract.user_key("workspace_name") == "team"
        assert contract.user_key("email") == "email"


class TestOverlay:
    def test_no_overlay_means_the_built_in_contract(self) -> None:
        assert load_contract(None) is DEFAULT_CONTRACT
        assert load_contract("") is DEFAULT_CONTRACT

    def test_a_new_entity_needs_no_code(self, tmp_path: Path) -> None:
        """The scalability claim, as an assertion."""
        path = tmp_path / "contract.json"
        path.write_text(
            json.dumps(
                {
                    "entities": {
                        "focus_session": {
                            "method": "PUT",
                            "path": "/focus-sessions/{client_id}",
                            "summary": "Deep-work block.",
                            "body_fields": ["id", "started_at", "duration_seconds"],
                        }
                    }
                }
            )
        )

        contract = load_contract(path)
        endpoint = contract.entity("focus_session")

        assert endpoint is not None
        assert endpoint.url(client_id=12) == "/focus-sessions/12"
        assert endpoint.body(
            {"id": 12, "started_at": "t", "duration_seconds": 900, "note": "x"},
            client_id=12,
        ) == {"id": 12, "started_at": "t", "duration_seconds": 900}
        # and the built-in entities are untouched
        assert contract.entity("work_session") == DEFAULT_CONTRACT.entity(
            "work_session"
        )

    def test_a_path_can_be_repointed(self, tmp_path: Path) -> None:
        path = tmp_path / "contract.json"
        path.write_text(json.dumps({"me": {"path": "/v2/whoami"}}))

        contract = load_contract(path)

        assert contract.identity.path == "/v2/whoami"
        assert contract.source == str(path)

    def test_a_new_binary_payload_needs_no_code(self, tmp_path: Path) -> None:
        path = tmp_path / "contract.json"
        path.write_text(
            json.dumps(
                {
                    "file_endpoints": {
                        "clip": {
                            "method": "POST",
                            "path": "/clips/{client_id}/file",
                            "form_fields": ["client_id"],
                            "file_field": "blob",
                            "file_content_type": "video/mp4",
                        }
                    }
                }
            )
        )

        endpoint = load_contract(path).file_endpoint("clip")

        assert endpoint is not None
        assert endpoint.file_field == "blob"
        assert endpoint.file_content_type == "video/mp4"
        assert endpoint.url(client_id=3) == "/clips/3/file"

    def test_a_body_field_list_can_be_widened_to_passthrough(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "contract.json"
        path.write_text(json.dumps({"entities": {"break": {"body_fields": None}}}))

        contract = load_contract(path)
        endpoint = contract.entity("break")

        assert endpoint is not None
        assert endpoint.body_fields is None

    @pytest.mark.parametrize(
        ("payload", "message"),
        [
            ({"entities": {"x": "not-an-object"}}, "must be an object"),
            ({"entities": {"x": {"path": "no-leading-slash"}}}, "starting with '/'"),
            ({"entities": {"x": {"body_fields": "nope"}}}, "must be a list"),
            ({"me": {"body_fields": 5}}, "must be a list"),
            ({"entities": []}, "'entities' must be an object"),
            ("[]", "must contain a JSON object"),
            ("{not json", "not readable JSON"),
        ],
    )
    def test_a_malformed_overlay_is_a_startup_error(
        self, tmp_path: Path, payload: Any, message: str
    ) -> None:
        path = tmp_path / "contract.json"
        path.write_text(payload if isinstance(payload, str) else json.dumps(payload))

        with pytest.raises(ConfigurationError, match=message):
            load_contract(path)

    def test_a_missing_overlay_is_a_startup_error(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError, match="does not exist"):
            load_contract(tmp_path / "absent.json")


def _contract_from(overlay: dict[str, Any], tmp_path: Path) -> ApiContract:
    path = tmp_path / "overlay.json"
    path.write_text(json.dumps(overlay))
    return load_contract(path)


if TYPE_CHECKING:
    from pathlib import Path
