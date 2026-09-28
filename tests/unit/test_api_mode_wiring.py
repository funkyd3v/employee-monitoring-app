"""Unit tests for api-mode wiring: settings, container, and the auth service.

Backend-readiness is only real if selecting the backend is a *configuration*
change, so these tests pin two things:

* a misconfigured ``mode="api"`` fails loudly at bootstrap instead of quietly
  falling back to the dummy providers (an employee who believes their data is
  uploading must never be recording into a local-only void);
* the token flows from login to the sync provider and is withdrawn on logout.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from app.config.settings import AppSettings, LocalConfig
from app.core.container import Container
from app.core.exceptions import ConfigurationError
from app.domain.auth.auth import AuthProvider
from app.domain.auth.workspace import LocalWorkspaceProvider
from app.domain.policy.provider import LocalConfigProvider
from app.domain.sync.provider import SyncProvider
from app.infrastructure.network.auth_adapter import (
    ApiAuthProvider,
    ApiWorkspaceProvider,
)
from app.infrastructure.network.config_adapter import ApiConfigProvider
from app.infrastructure.network.sync_adapter import ApiSyncProvider, DummySyncProvider
from app.services.policy_service import PolicyService
from pydantic import ValidationError

if TYPE_CHECKING:
    from pathlib import Path

BASE_URL = "http://127.0.0.1:8000/api/v1"


class TestApiBaseUrlValidation:
    def test_blank_is_allowed(self) -> None:
        """local mode never reads it, so an empty default is correct."""
        assert LocalConfig().api_base_url == ""

    @pytest.mark.parametrize(
        "value",
        [
            "http://127.0.0.1:8000/api/v1",
            "https://monitoring.example.com/api/v1",
            "http://localhost:8000",
        ],
    )
    def test_accepts_http_and_https(self, value: str) -> None:
        assert LocalConfig(api_base_url=value).api_base_url == value.rstrip("/")

    def test_strips_trailing_slash(self) -> None:
        assert LocalConfig(api_base_url=f"{BASE_URL}/").api_base_url == BASE_URL

    @pytest.mark.parametrize(
        "value",
        ["ftp://host/api", "ws://host/api", "127.0.0.1:8000", "http://", "://x"],
    )
    def test_rejects_anything_that_is_not_an_http_url(self, value: str) -> None:
        with pytest.raises(ValidationError):
            LocalConfig(api_base_url=value)

    def test_rejects_non_positive_timeouts(self) -> None:
        with pytest.raises(ValidationError):
            LocalConfig(api_timeout_seconds=0)
        with pytest.raises(ValidationError):
            LocalConfig(api_connect_timeout_seconds=-1)

    def test_passthrough_from_app_settings(self) -> None:
        settings = AppSettings(local=LocalConfig(api_base_url=BASE_URL))
        assert settings.api_base_url == BASE_URL


class TestContainerWiring:
    def _container(self, tmp_path: Path, **local: object) -> Container:
        container = Container(
            AppSettings(
                local=LocalConfig(
                    data_dir=tmp_path / "data",
                    credential_backend="none",
                    log_level="DEBUG",
                    **local,  # type: ignore[arg-type]
                )
            )
        )
        container.open_database()
        return container

    def test_local_mode_keeps_the_dummy_providers(self, tmp_path: Path) -> None:
        container = self._container(tmp_path)
        try:
            assert isinstance(container.sync_provider, DummySyncProvider)
            assert not isinstance(container.auth_service._provider, ApiAuthProvider)
        finally:
            container.close_database()

    def test_api_mode_selects_the_http_providers(self, tmp_path: Path) -> None:
        container = self._container(tmp_path, mode="api", api_base_url=BASE_URL)
        try:
            assert isinstance(container.sync_provider, ApiSyncProvider)
            assert isinstance(container.auth_service._provider, ApiAuthProvider)
        finally:
            container.close_database()

    def test_api_mode_wires_the_workspace_and_policy_providers(
        self, tmp_path: Path
    ) -> None:
        """The two server-owned settings arrive through interfaces too, so the
        UI and services never learn an endpoint exists."""
        container = self._container(tmp_path, mode="api", api_base_url=BASE_URL)
        try:
            assert isinstance(container.workspace_provider, ApiWorkspaceProvider)
            assert isinstance(container.config_provider, ApiConfigProvider)
            assert isinstance(container.policy_service, PolicyService)
        finally:
            container.close_database()

    def test_local_mode_uses_the_offline_implementations(self, tmp_path: Path) -> None:
        container = self._container(tmp_path)
        try:
            assert isinstance(container.workspace_provider, LocalWorkspaceProvider)
            assert isinstance(container.config_provider, LocalConfigProvider)
            assert container.workspace_provider.fetch_workspace() is None
        finally:
            container.close_database()

    def test_every_api_mode_provider_shares_one_http_client(
        self, tmp_path: Path
    ) -> None:
        """One pooled client means one close() to get right at shutdown."""
        container = self._container(tmp_path, mode="api", api_base_url=BASE_URL)
        try:
            clients = {
                id(container._build_api_client()),
                id(container._build_api_client()),
            }
            assert len(clients) == 1
        finally:
            container.close_database()

    def test_a_broken_contract_file_fails_at_bootstrap(self, tmp_path: Path) -> None:
        contract = tmp_path / "contract.json"
        contract.write_text("{ this is not json")

        with pytest.raises(ConfigurationError, match="api_contract_file"):
            Container(
                AppSettings(
                    local=LocalConfig(
                        data_dir=tmp_path / "data",
                        mode="api",
                        api_base_url=BASE_URL,
                        api_contract_file=str(contract),
                    )
                )
            )

    def test_a_contract_file_is_loaded_in_api_mode_only(self, tmp_path: Path) -> None:
        contract = tmp_path / "contract.json"
        contract.write_text('{"me": {"path": "/v2/whoami"}}')

        # Local mode never opens a socket, so a broken local .env must not be
        # able to stop the app from starting.
        container = self._container(
            tmp_path, api_contract_file=str(tmp_path / "missing.json")
        )
        container.close_database()

        container = self._container(
            tmp_path, mode="api", api_base_url=BASE_URL, api_contract_file=str(contract)
        )
        try:
            assert container._api_contract is not None
            assert container._api_contract.identity.path == "/v2/whoami"
        finally:
            container.close_database()

    def test_api_mode_without_a_base_url_fails_loudly(self, tmp_path: Path) -> None:
        """Silently falling back to the dummy would lose every upload without
        telling anyone."""
        with pytest.raises(ConfigurationError, match="api_base_url"):
            Container(
                AppSettings(
                    local=LocalConfig(
                        data_dir=tmp_path / "data", mode="api", api_base_url=""
                    )
                )
            )

    def test_api_mode_refuses_the_plaintext_dev_file_credential_store(
        self, tmp_path: Path
    ) -> None:
        from app.domain.auth.credential_store import CredentialStoreUnavailableError

        with pytest.raises(CredentialStoreUnavailableError, match="dev-file"):
            Container(
                AppSettings(
                    local=LocalConfig(
                        data_dir=tmp_path / "data",
                        mode="api",
                        api_base_url=BASE_URL,
                        credential_backend="dev-file",
                    )
                )
            )


class TestTokenFlowsToTheProvider:
    def test_login_publishes_and_logout_withdraws_the_token(
        self, tmp_path: Path
    ) -> None:
        """The whole reason the holder exists: the sync provider must be able
        to authenticate without the sync layer knowing about auth."""
        container = Container(
            AppSettings(
                local=LocalConfig(
                    data_dir=tmp_path / "data",
                    credential_backend="none",
                    log_level="DEBUG",
                )
            )
        )
        container.open_database()
        try:
            holder = container.token_holder
            assert holder.get() is None

            # Log in against the dummy provider in local mode; the holder is
            # provider-agnostic, so this exercises AuthService's side only.
            container.auth_service.login("employee@example.com", "secret")
            assert holder.get() is not None

            container.auth_service.logout()
            assert holder.get() is None
        finally:
            container.close_database()

    def test_shutdown_also_withdraws_the_token(self, tmp_path: Path) -> None:
        container = Container(
            AppSettings(
                local=LocalConfig(
                    data_dir=tmp_path / "data",
                    credential_backend="none",
                    log_level="DEBUG",
                )
            )
        )
        container.open_database()
        try:
            container.auth_service.login("employee@example.com", "secret")
            assert container.token_holder.has_token() is True

            container.auth_service.shutdown()
            assert container.token_holder.get() is None
        finally:
            container.close_database()

    def test_container_exposes_the_provider_contracts_only(
        self, tmp_path: Path
    ) -> None:
        """Business logic depends on the ABCs, never a concrete adapter."""
        container = Container(
            AppSettings(
                local=LocalConfig(
                    data_dir=tmp_path / "data",
                    credential_backend="none",
                    log_level="DEBUG",
                )
            )
        )
        container.open_database()
        try:
            assert isinstance(container.sync_provider, SyncProvider)
            assert isinstance(container.auth_service._provider, AuthProvider)
        finally:
            container.close_database()
