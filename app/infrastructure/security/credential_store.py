"""Credential store implementations behind :class:`CredentialStore`.

Chosen by the container from ``settings.credential_backend``:

* :class:`KeyringCredentialStore`  — the production path. Windows Credential
  Manager via ``keyring`` (docs/SECURITY_PRIVACY.md). Raises
  :class:`CredentialStoreUnavailableError` when the OS has no keyring
  backend, so storage never silently degrades to plaintext.
* :class:`DevFileCredentialStore` — explicit opt-in for development machines
  without a keyring backend. Writes a 0600 file under the data directory and
  is refused in ``api`` mode. Never selected implicitly.
* :class:`EphemeralCredentialStore` — in-memory only; used so the agent can
  run fully offline-first when no secure store exists, at the cost of
  re-login after restart. Never touches disk.

:func:`build_credential_store` applies the selection policy.
"""

from __future__ import annotations

import stat
from typing import TYPE_CHECKING, Protocol

import keyring as _keyring
from keyring.backends.fail import Keyring as _FailKeyring

from app.config.constants import AUTH_TOKEN_FILENAME, CONFIG_DIR
from app.core.logging import get_logger
from app.domain.auth.credential_store import (
    CredentialStore,
    CredentialStoreUnavailableError,
)

if TYPE_CHECKING:
    from pathlib import Path

    from app.config.settings import AppSettings

_SERVICE = "EmployeeMonitoring"
_ACCOUNT = "auth-token"


class _KeyringApi(Protocol):
    """The surface of ``keyring`` we use (injectable for tests)."""

    def get_keyring(self) -> object: ...

    def get_password(self, service: str, account: str) -> str | None: ...

    def set_password(self, service: str, account: str, value: str) -> None: ...

    def delete_password(self, service: str, account: str) -> None: ...


def _is_null_backend(keyring_api: _KeyringApi) -> bool:
    """True when the resolved keyring backend is the null/fail placeholder.

    ``keyring``'s explicit no-backend object (``keyring.backends.fail``) is
    a sentinel that always raises; detecting it is the only reliable way to
    know storage would silently fail if we tried it.
    """
    backend = keyring_api.get_keyring()
    return isinstance(backend, _FailKeyring)


class KeyringCredentialStore:
    """Stores the token in the OS credential manager via ``keyring``.

    ``keyring_api`` is injectable for tests; it must expose
    ``get_keyring``/``get_password``/``set_password``/``delete_password``.
    """

    backend = "keyring"

    def __init__(self, keyring_api: _KeyringApi | None = None) -> None:
        self._api = keyring_api or _keyring
        if _is_null_backend(self._api):
            raise CredentialStoreUnavailableError(
                "OS keyring has no backend — tokens would not persist "
                "securely; refusing to degrade to plaintext storage"
            )

    def save(self, token: str) -> None:
        try:
            self._api.set_password(_SERVICE, _ACCOUNT, token)
        except Exception as exc:
            raise CredentialStoreUnavailableError(
                f"could not store auth token in OS keyring: {exc}"
            ) from exc

    def load(self) -> str | None:
        try:
            return self._api.get_password(_SERVICE, _ACCOUNT)
        except Exception as exc:
            raise CredentialStoreUnavailableError(
                f"could not read auth token from OS keyring: {exc}"
            ) from exc

    def clear(self) -> None:
        if self.load() is None:
            return
        try:
            self._api.delete_password(_SERVICE, _ACCOUNT)
        except Exception as exc:
            raise CredentialStoreUnavailableError(
                f"could not remove auth token from OS keyring: {exc}"
            ) from exc


class DevFileCredentialStore:
    """Explicit dev-only token file (0600), never selected by ``auto``.

    Only reachable through ``settings.credential_backend == "dev-file"`` and
    refused in ``api`` mode by the container policy. The file lives in the
    per-user private data dir (``…/config/auth.token``).
    """

    backend = "dev-file"

    def __init__(self, path: Path) -> None:
        self._path = path

    def save(self, token: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(token, encoding="utf-8")
        self._path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    def load(self) -> str | None:
        try:
            return self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

    def clear(self) -> None:
        try:
            self._path.unlink()
        except FileNotFoundError:
            return None


class EphemeralCredentialStore:
    """In-memory token storage; nothing ever touches disk.

    Used when the machine has no secure store and persistence is not
    expected (restart requires re-login). Keeps the agent fully functional
    offline-first instead of crashing or degrading to plaintext.
    """

    backend = "none"

    def __init__(self) -> None:
        self._token: str | None = None

    def save(self, token: str) -> None:
        self._token = token

    def load(self) -> str | None:
        return self._token

    def clear(self) -> None:
        self._token = None


def dev_token_path(settings: AppSettings) -> Path:
    """Where the dev-only token file lives under the data directory."""
    return settings.subdir(CONFIG_DIR) / AUTH_TOKEN_FILENAME


def build_credential_store(
    settings: AppSettings, keyring_api: _KeyringApi | None = None
) -> CredentialStore:
    """Select the credential store per ``settings.credential_backend``.

    Policy (docs/SECURITY_PRIVACY.md):
    * ``auto`` (default) — the OS keyring when present, otherwise
      :class:`EphemeralCredentialStore` (never plaintext on disk).
    * ``keyring`` — require the OS keyring, raise if unavailable.
    * ``dev-file`` — explicit development opt-in; refused in ``api`` mode.
    * ``none`` — in-memory only.

    ``keyring_api`` is injectable for tests.
    """
    backend = settings.local.credential_backend
    logger = get_logger("security")

    if backend == "none":
        return EphemeralCredentialStore()

    if backend == "dev-file":
        if settings.mode == "api":
            raise CredentialStoreUnavailableError(
                "credential_backend='dev-file' is not allowed in api mode"
            )
        return DevFileCredentialStore(dev_token_path(settings))

    if backend == "keyring":
        return KeyringCredentialStore(keyring_api)

    try:
        return KeyringCredentialStore(keyring_api)
    except CredentialStoreUnavailableError:
        logger.warning(
            "secure credential storage unavailable (no OS keyring backend) — "
            "session tokens will not persist across restarts"
        )
        return EphemeralCredentialStore()
