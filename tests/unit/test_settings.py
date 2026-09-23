"""Unit tests for the configuration system."""

from __future__ import annotations

from pathlib import Path

import pytest
from app.config.constants import DEFAULT_IDLE_THRESHOLD_SECONDS
from app.config.settings import AppSettings, LocalConfig, ServerPolicy


def test_local_defaults() -> None:
    settings = LocalConfig(_env_file=None)
    assert settings.mode == "local"
    assert settings.log_level == "INFO"
    assert settings.data_dir is not None


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EM_MODE", "api")
    monkeypatch.setenv("EM_LOG_LEVEL", "DEBUG")
    settings = LocalConfig(_env_file=None)
    assert settings.mode == "api"
    assert settings.log_level == "DEBUG"


def test_data_dir_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "custom"
    monkeypatch.setenv("EM_DATA_DIR", str(target))
    settings = LocalConfig(_env_file=None)
    assert settings.data_dir == target


def test_data_dir_expands_tilde() -> None:
    settings = LocalConfig(data_dir="~/monitoring", _env_file=None)
    assert str(settings.data_dir).startswith(str(Path("~").expanduser()))


def test_invalid_log_level_rejected() -> None:
    with pytest.raises(ValueError, match="log_level"):
        LocalConfig(log_level="VERBOSE", _env_file=None)


def test_server_policy_defaults() -> None:
    policy = ServerPolicy()
    assert policy.screenshot_interval_seconds == 60
    assert policy.idle_threshold_seconds == DEFAULT_IDLE_THRESHOLD_SECONDS


def test_screenshot_interval_minimum_enforced() -> None:
    with pytest.raises(ValueError, match="at least"):
        ServerPolicy(screenshot_interval_seconds=59)
    # Boundary accepted.
    assert (
        ServerPolicy(screenshot_interval_seconds=60).screenshot_interval_seconds == 60
    )


def test_app_settings_composition(app_settings: AppSettings) -> None:
    assert app_settings.screenshot_interval_seconds == 60
    assert app_settings.idle_threshold_seconds == 300
    assert app_settings.mode == "local"
    assert app_settings.subdir("logs") == app_settings.data_dir / "logs"
