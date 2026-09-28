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


class TestDotEnvLookup:
    """`.env` must be found even when the app is launched from elsewhere.

    pydantic-settings resolves a relative ``env_file`` against the current
    working directory, so a config file next to the code is silently ignored
    when the app is started from a shortcut or a different shell — api mode then
    falls back to local, and the employee is told their password is wrong.
    """

    def test_the_source_tree_is_searched_absolutely(self) -> None:
        from app.config.settings import _env_file_sources

        base, _override = _env_file_sources()

        assert Path(base).is_absolute()
        assert Path(base).name == ".env"
        assert Path(base).parent == Path(__file__).resolve().parents[2]

    def test_the_working_directory_copy_overrides_it(self) -> None:
        from app.config.settings import _env_file_sources

        # Last source wins in pydantic-settings, so the more specific
        # location must be the relative one.
        assert _env_file_sources()[-1] == ".env"

    def test_a_dot_env_in_the_working_directory_is_applied(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / ".env").write_text("EM_MODE=api\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("EM_MODE", raising=False)

        assert LocalConfig(_env_file=".env").mode == "api"

    def test_a_dot_env_can_still_turn_api_mode_back_off(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / ".env").write_text("EM_MODE=local\n")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("EM_MODE", raising=False)

        assert LocalConfig(_env_file=".env").mode == "local"
