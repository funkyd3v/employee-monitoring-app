"""Typed configuration.

Split into two sources per the backend-readiness strategy
(docs/ARCHITECTURE.md):

* :class:`LocalConfig`  — read from the environment / ``.env`` on every boot.
* :class:`ServerPolicy` — settings a backend will eventually push. Their
  *shape* is defined now (contract-first) and they default to the same
  values the plan assumes, so later only the source of truth changes.

:class:`AppSettings` composes both and is the single object services receive.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qsl, urlparse

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config.constants import (
    APP_NAME_SHORT,
    DEFAULT_CLEANUP_POLL_SECONDS,
    DEFAULT_IDLE_THRESHOLD_SECONDS,
    DEFAULT_RETENTION_DAYS,
    DEFAULT_RETENTION_MEGABYTES,
    DEFAULT_SYNC_BATCH_LIMIT,
    DEFAULT_SYNC_POLL_SECONDS,
    MIN_SCREENSHOT_INTERVAL_SECONDS,
)
from app.core.exceptions import ConfigurationError

RuntimeMode = Literal["local", "api"]

_ENV_PREFIX = "EM_"
_ENV_FILE = ".env"

# Query-parameter names that would carry a live secret inside the base URL.
# The URL is bundled into a distributed executable and is visible in DNS and
# TLS SNI, so anything credential-shaped in it is a credential in plain sight.
_SECRET_QUERY_HINTS: tuple[str, ...] = (
    "token",
    "secret",
    "password",
    "passwd",
    "api_key",
    "apikey",
    "access_key",
    "signature",
    "credential",
)


def _is_loopback(host: str) -> bool:
    """True for hosts that never leave the machine."""
    host = host.strip().lower().strip("[]")
    return host in {"localhost", "::1"} or host.startswith("127.")


def _env_file_sources() -> tuple[str, ...]:
    """Where to look for ``.env``, least specific first so later ones win.

    A relative ``env_file`` is resolved against the *current working
    directory*, so a ``.env`` sitting next to the code is invisible the moment
    the app is launched from a shortcut, a scheduled task, or a different
    shell — and api mode then falls back to local with no warning, which looks
    exactly like a wrong password.

    A packaged build has no source tree at all, so it gets one more place: a
    default bundled *inside* the executable (installer/build.spec ships
    ``.env.example`` as ``.env`` so a downloaded build is never silently
    local-only), plus a ``.env`` dropped beside the ``.exe`` as the deliberate
    per-deployment override.
    """
    sources: list[str] = []

    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        # The default shipped inside the executable, weakest of all: a
        # packaged build with no other config must not silently run local.
        sources.append(str(Path(bundle_dir) / _ENV_FILE))

    sources.append(str(Path(__file__).resolve().parents[2] / _ENV_FILE))
    sources.append(_ENV_FILE)

    if bundle_dir:
        # Strongest: a .env deliberately placed beside the .exe is the
        # per-deployment override, and must win even over a stray source tree.
        sources.append(str(Path(sys.executable).resolve().parent / _ENV_FILE))

    return tuple(sources)


_ENV_FILES: tuple[str, ...] = _env_file_sources()


def default_data_dir() -> Path:
    """Per-user data directory, never the install directory.

    Windows: ``%LOCALAPPDATA%\\EmployeeMonitoring``
    other:   ``~/.local/share/employee-monitoring-agent`` (development only)
    """
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA")
        if root:
            return Path(root) / APP_NAME_SHORT
    xdg_data = os.environ.get("XDG_DATA_HOME")
    if xdg_data:
        return Path(xdg_data) / "employee-monitoring-agent"
    return Path.home() / ".local" / "share" / "employee-monitoring-agent"


class LocalConfig(BaseSettings):
    """Machine-local settings, read from the environment / ``.env``."""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
        env_prefix=_ENV_PREFIX,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    mode: RuntimeMode = "local"
    log_level: str = Field(
        default="INFO", pattern=r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$"
    )
    log_max_bytes: int = 5 * 1024 * 1024
    log_backup_count: int = 5

    data_dir: Path = Field(default_factory=default_data_dir)

    # Where the auth token lives (docs/SECURITY_PRIVACY.md). Production is
    # "auto": the OS keyring when available, otherwise non-persistent memory
    # (never plaintext on disk). "dev-file" is an explicit opt-in for
    # development machines with no keyring backend.
    credential_backend: Literal["auto", "keyring", "dev-file", "none"] = "auto"

    # Dev-only dummy authentication (Phase 3). Must never be a default in
    # production code paths.
    dummy_email: str = "employee@example.com"
    dummy_password: str = ""

    # ── Backend API (mode: "api") ──────────────────────────────────────
    # Base URL including the version prefix, e.g.
    # ``http://127.0.0.1:8000/api/v1``. Only read in api mode; the dummy
    # providers never touch the network.
    api_base_url: str = ""

    # Connect timeout is deliberately short: a background sync worker must
    # not stall on an unreachable host. The read/write timeout is longer
    # because a screenshot upload of several MB is a legitimate slow write.
    api_connect_timeout_seconds: float = 5.0
    api_timeout_seconds: float = 30.0

    # Optional JSON overlay describing the backend's wire shape (paths, field
    # lists, user-envelope keys). Empty means the built-in contract documented
    # in docs/API_CONTRACT.md. A path that does not exist is a startup error,
    # never a silent fallback — see load_contract().
    api_contract_file: str = ""

    @model_validator(mode="before")
    @classmethod
    def _reject_credential_bearing_url(cls, data: object) -> object:
        """Refuse a base URL carrying a credential — before pydantic can echo it.

        A field validator is the wrong place for this. pydantic appends
        ``input_value=<the whole url>`` to every field validation error, so a
        URL like ``https://user:pass@host/api`` would put the password into the
        exception text, and from there into logs and error dialogs — the one
        place it must never reach.

        Raising a non-``ValueError`` from a before-validator propagates
        unwrapped, so the offending value is never quoted back.
        """
        if not isinstance(data, dict):
            return data
        raw = data.get("api_base_url")
        if not isinstance(raw, str) or not raw.strip():
            return data
        parsed = urlparse(raw.strip())
        if parsed.username or parsed.password:
            raise ConfigurationError(
                "api_base_url must not embed a username or password; the agent "
                "signs in with the employee's own credentials"
            )
        for name, _value in parse_qsl(parsed.query, keep_blank_values=True):
            if any(hint in name.lower() for hint in _SECRET_QUERY_HINTS):
                raise ConfigurationError(
                    "api_base_url must not carry a credential-like query "
                    "parameter; the agent authenticates with the employee's own "
                    "credentials"
                )
        return data

    @field_validator("api_base_url")
    @classmethod
    def _validate_api_base_url(cls, value: str) -> str:
        """Reject base URLs that would send the session token in cleartext.

        Credential-bearing URLs never reach here — they are rejected by
        :meth:`_reject_credential_bearing_url` first, so echoing the input
        value in the error below is safe.
        """
        value = value.strip().rstrip("/")
        if not value:
            return value
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("api_base_url must start with http:// or https://")
        if not parsed.netloc:
            raise ValueError("api_base_url must include a host")

        if parsed.scheme == "http" and not _is_loopback(parsed.hostname or ""):
            raise ValueError(
                "api_base_url must use https:// for a non-local host; over "
                "http the password and session token are sent in cleartext"
            )
        return value

    @field_validator("api_connect_timeout_seconds", "api_timeout_seconds")
    @classmethod
    def _validate_timeouts(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("API timeouts must be greater than zero")
        return value

    @field_validator("data_dir", mode="before")
    @classmethod
    def _expand_user(cls, value: object) -> object:
        if isinstance(value, str):
            return Path(os.path.expandvars(value)).expanduser()
        return value


class ServerPolicy(BaseModel):
    """Settings a backend will push later (contract-first).

    Today these defaults from the plan are authoritative; tomorrow a
    ``RemoteConfigProvider`` pushes the same JSON and this object is the
    read model. Services must never depend on *where* these come from.
    """

    screenshot_interval_seconds: int = MIN_SCREENSHOT_INTERVAL_SECONDS
    idle_threshold_seconds: int = DEFAULT_IDLE_THRESHOLD_SECONDS
    local_retention_megabytes: int = DEFAULT_RETENTION_MEGABYTES
    sync_poll_seconds: int = DEFAULT_SYNC_POLL_SECONDS
    cleanup_poll_seconds: int = DEFAULT_CLEANUP_POLL_SECONDS
    sync_batch_limit: int = DEFAULT_SYNC_BATCH_LIMIT
    retention_days: int = DEFAULT_RETENTION_DAYS

    @field_validator("screenshot_interval_seconds")
    @classmethod
    def _enforce_minimum_interval(cls, value: int) -> int:
        if value < MIN_SCREENSHOT_INTERVAL_SECONDS:
            raise ValueError(
                "screenshot interval must be at least "
                f"{MIN_SCREENSHOT_INTERVAL_SECONDS} seconds"
            )
        return value

    @field_validator("sync_poll_seconds", "cleanup_poll_seconds", "retention_days")
    @classmethod
    def _enforce_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("value must be >= 0")
        return value

    @field_validator("sync_batch_limit")
    @classmethod
    def _enforce_batch_limit(cls, value: int) -> int:
        if value < 1:
            raise ValueError("sync_batch_limit must be >= 1")
        return value


class AppSettings:
    """Composite settings object handed to services and the container."""

    def __init__(
        self, local: LocalConfig | None = None, server: ServerPolicy | None = None
    ) -> None:
        self.local = local or LocalConfig()
        self.server = server or ServerPolicy()

    # Convenience passthroughs used by most services.
    @property
    def data_dir(self) -> Path:
        return self.local.data_dir

    @property
    def screenshot_interval_seconds(self) -> int:
        return self.server.screenshot_interval_seconds

    @property
    def idle_threshold_seconds(self) -> int:
        return self.server.idle_threshold_seconds

    @property
    def sync_poll_seconds(self) -> int:
        return self.server.sync_poll_seconds

    @property
    def cleanup_poll_seconds(self) -> int:
        return self.server.cleanup_poll_seconds

    @property
    def sync_batch_limit(self) -> int:
        return self.server.sync_batch_limit

    @property
    def retention_days(self) -> int:
        return self.server.retention_days

    @property
    def mode(self) -> RuntimeMode:
        return self.local.mode

    @property
    def api_base_url(self) -> str:
        return self.local.api_base_url

    def subdir(self, relative: str) -> Path:
        """Resolve a per-user storage subdirectory (e.g. ``logs``)."""
        return self.data_dir / relative
