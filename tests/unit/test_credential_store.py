"""Unit tests for credential stores and the backend-selection policy."""

from __future__ import annotations

import stat
from typing import TYPE_CHECKING

import pytest
from app.config.constants import AUTH_TOKEN_FILENAME, CONFIG_DIR
from app.config.settings import AppSettings, LocalConfig, ServerPolicy
from app.domain.auth.credential_store import CredentialStoreUnavailableError
from app.infrastructure.security.credential_store import (
    DevFileCredentialStore,
    EphemeralCredentialStore,
    KeyringCredentialStore,
    build_credential_store,
    dev_token_path,
)
from keyring.backends.fail import Keyring as FailKeyring

if TYPE_CHECKING:
    from pathlib import Path

TOKEN = "dv1.eyJlbWFpbCI6ImVtcGxveWVlQGV4YW1wbGUuY29tIn0"


class FakeKeyring:
    """Dict-backed stand-in for the ``keyring`` module surface."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], str] = {}

    def get_keyring(self):
        return self

    def get_password(self, _service: str, _account: str) -> str | None:
        return self._rows.get((_service, _account))

    def set_password(self, _service: str, _account: str, value: str) -> None:
        self._rows[(_service, _account)] = value

    def delete_password(self, _service: str, _account: str) -> None:
        self._rows.pop((_service, _account), None)


class FailingKeyring:
    """A keyring whose backend is the fail placeholder (no storage)."""

    def get_keyring(self):
        return FailKeyring()

    def get_password(self, _service: str, _account: str) -> str | None:
        return FailKeyring().get_password(_service, _account)

    def set_password(self, _service: str, _account: str, _value: str) -> None:
        raise CredentialStoreUnavailableError("no backend")

    def delete_password(self, _service: str, _account: str) -> None:
        raise CredentialStoreUnavailableError("no backend")


class ExplodingKeyring:
    """A backend that exists but throws on every operation."""

    def get_keyring(self):
        return object()

    def get_password(self, _service: str, _account: str) -> str | None:
        raise RuntimeError("credential manager unreachable")

    def set_password(self, _service: str, _account: str, _value: str) -> None:
        raise RuntimeError("credential manager unreachable")

    def delete_password(self, _service: str, _account: str) -> None:
        raise RuntimeError("credential manager unreachable")


def make_settings(tmp_path: Path, **local_kwargs: object) -> AppSettings:
    local = LocalConfig(
        data_dir=tmp_path / "data",
        **local_kwargs,  # type: ignore[arg-type] # pydantic accepts test kwargs
    )
    return AppSettings(local=local, server=ServerPolicy())


class TestKeyringCredentialStore:
    def test_roundtrip(self) -> None:
        keyring = FakeKeyring()
        store = KeyringCredentialStore(keyring)
        assert store.backend == "keyring"
        store.save(TOKEN)
        assert store.load() == TOKEN
        store.clear()
        assert store.load() is None

    def test_clear_without_token_is_noop(self) -> None:
        store = KeyringCredentialStore(FakeKeyring())
        assert store.load() is None
        store.clear()
        assert store.load() is None

    def test_fail_backend_refused_at_construction(self) -> None:
        with pytest.raises(CredentialStoreUnavailableError, match="no backend"):
            KeyringCredentialStore(FailingKeyring())

    def test_operation_errors_surface_as_unavailable(self) -> None:
        store = KeyringCredentialStore(ExplodingKeyring())
        with pytest.raises(CredentialStoreUnavailableError):
            store.save(TOKEN)
        with pytest.raises(CredentialStoreUnavailableError):
            store.load()


class TestDevFileCredentialStore:
    def test_roundtrip_and_permissions(self, tmp_path: Path) -> None:
        store = DevFileCredentialStore(tmp_path / "config" / AUTH_TOKEN_FILENAME)
        store.save(TOKEN)
        token_file = tmp_path / "config" / AUTH_TOKEN_FILENAME
        assert token_file.read_text(encoding="utf-8") == TOKEN
        assert stat.S_IMODE(token_file.stat().st_mode) == stat.S_IRUSR | stat.S_IWUSR
        assert store.load() == TOKEN
        store.clear()
        assert store.load() is None
        assert not token_file.exists()

    def test_load_missing_file_returns_none(self, tmp_path: Path) -> None:
        store = DevFileCredentialStore(tmp_path / "nope" / "auth.token")
        assert store.load() is None
        assert store.backend == "dev-file"


class TestEphemeralCredentialStore:
    def test_roundtrip_and_clear(self) -> None:
        store = EphemeralCredentialStore()
        assert store.backend == "none"
        store.save(TOKEN)
        assert store.load() == TOKEN
        store.clear()
        assert store.load() is None

    def test_never_different_instances_share_state(self) -> None:
        a, b = EphemeralCredentialStore(), EphemeralCredentialStore()
        a.save(TOKEN)
        assert b.load() is None


class TestBuildCredentialStore:
    def test_none_backend_is_ephemeral(self, tmp_path: Path) -> None:
        settings = make_settings(tmp_path, credential_backend="none")
        assert build_credential_store(settings).backend == "none"

    def test_dev_file_backend_uses_data_dir(self, tmp_path: Path) -> None:
        settings = make_settings(tmp_path, credential_backend="dev-file")
        store = build_credential_store(settings)
        assert store.backend == "dev-file"
        store.save(TOKEN)
        assert (tmp_path / "data" / CONFIG_DIR / AUTH_TOKEN_FILENAME).exists()
        assert store.load() == TOKEN

    def test_dev_file_backend_refused_in_api_mode(self, tmp_path: Path) -> None:
        settings = make_settings(tmp_path, credential_backend="dev-file", mode="api")
        with pytest.raises(
            CredentialStoreUnavailableError, match="not allowed in api mode"
        ):
            build_credential_store(settings)

    def test_keyring_backend_required_when_no_backend(self, tmp_path: Path) -> None:
        settings = make_settings(tmp_path, credential_backend="keyring")
        with pytest.raises(CredentialStoreUnavailableError, match="no backend"):
            build_credential_store(settings, keyring_api=FailingKeyring())

    def test_auto_downgrades_to_ephemeral_when_no_backend(self, tmp_path: Path) -> None:
        settings = make_settings(tmp_path, credential_backend="auto")
        store = build_credential_store(settings, keyring_api=FailingKeyring())
        assert store.backend == "none"

    def test_auto_prefers_keyring_when_available(self, tmp_path: Path) -> None:
        settings = make_settings(tmp_path, credential_backend="auto")
        store = build_credential_store(settings, keyring_api=FakeKeyring())
        assert store.backend == "keyring"

    def test_dev_token_path_resolves_under_data_dir(self, tmp_path: Path) -> None:
        settings = make_settings(tmp_path)
        assert dev_token_path(settings) == (
            tmp_path / "data" / CONFIG_DIR / AUTH_TOKEN_FILENAME
        )
