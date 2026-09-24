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
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator
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

RuntimeMode = Literal["local", "api"]

_ENV_PREFIX = "EM_"
_ENV_FILE = ".env"


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
        env_file=_ENV_FILE,
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

    def subdir(self, relative: str) -> Path:
        """Resolve a per-user storage subdirectory (e.g. ``logs``)."""
        return self.data_dir / relative
